# Engineering notes

Design decisions, the reasoning behind them, and the things that went wrong.
Written so the reasoning survives after the code stops being fresh.

---

## Why an explicit graph instead of an autonomous agent loop

An autonomous loop cannot give you reproducibility, a cost bound, or a
defensible audit trail — and for a compliance tool the audit trail *is* the
deliverable. If a client asks "why did you mark control 47 as partial?", the
answer has to be "node 4 ran with this input and produced this output", not
"the model decided to".

The trade-off is real: the graph cannot handle a case its designer did not
anticipate, where an autonomous loop might improvise. For governance work that
is the correct trade. For an open-ended research task it would not be.

## Why four of six nodes use no model at all

Intake, risk tiering, retrieval and reporting are deterministic. Risk tiering
in particular: it drives which controls are mandatory and how the report reads,
so it must be explainable rule by rule. Ask a model and you get a plausible
tier you cannot defend.

The general principle: putting a model where a rule will do is how agent
systems become unauditable and expensive. The model is used for the one thing
it is genuinely better at — reading prose evidence against a question — and
nowhere else.

## Why hallucinated findings are downgraded rather than dropped

This one is worth sitting with, because the obvious implementation is wrong.

The obvious approach: model produces an ungrounded finding, throw it away. But
throwing it away also removes it from the denominator, so coverage *rises* as
hallucination increases. The tool would score better the more it made things
up.

Downgrading to `not_assessable` keeps it in the denominator and drops the
numerator, so hallucination visibly costs you. The incentive inside the metric
matters more than the metric's definition.

## Why the critic can only downgrade

Asymmetric costs. A wrong *non-compliant* costs a human an hour of checking. A
wrong *compliant* costs a regulatory finding, because nobody looks at it again.

So the critic is deliberately biased pessimistic, and its prompt says so in as
many words: "Being agreeable when the evidence is thin is the failure mode you
exist to prevent." Models are trained toward helpfulness; a reviewer prompt has
to actively counteract that.

## Why two numbers instead of one

Every compliance dashboard wants one number. One number lets a platform with
40% coverage and a 95% weighted score look like a 95% platform, when in fact it
has barely been assessed. Coverage and posture measure different things —
evidence quality and compliance posture — and reporting only the second is how
these tools end up lying to the people paying for them.

## Why the report leads with what it could not assess

Same reasoning. Section 1 of every report lists every control with no usable
verdict, before any score. A governance report that buries its own blind spots
is worse than no report, because it converts ignorance into assurance.

## What the evaluation harness actually caught

This is the part worth telling honestly, because it is what the harness is for.

First run: **70% status accuracy, 16.7% over-claim rate** — a failing grade
against the thresholds. Three real defects:

1. **Substring matching.** The indicator `"implemented"` matched inside
   `"Not implemented"`, flipping a failing control to partial. Fixed with
   word-boundary matching plus explicit negation phrases.
2. **Negation had no scope.** A negator anywhere in the evidence discounted
   every positive term. Fixed with a 40-character clause-bounded window.
3. **Presence beat weight.** Evidence with one positive and three negation
   phrases scored *partial*. For a rights-fulfilment control, partial
   fulfilment means the right was not delivered. Fixed with a weight-of-evidence
   rule: more negation than implementation is `non_compliant`.

After fixing the engine: **100% accuracy, 0% over-claim**. The tempting
alternative was lowering the thresholds, which would have hidden all three bugs
and produced a green build with a worse product. Regression tests for all three
are in `tests/test_llm_router.py`.

## Why hybrid retrieval, with the weight measured rather than asserted

Pure vector search underperforms here because the corpus is full of terms that
must match exactly — *Art. 22*, *cross-border transfer*, *pgvector*, *DPIA*.
Pure BM25 misses the paraphrases clients actually write.

Rather than assert that fusion helps, there is a test that fails if fused
retrieval is ever worse than either half alone. If the complexity stops paying
for itself, CI says so.

Light stemming was added after measuring: it took the benchmark from 7/8 to
8/8 by collapsing *erasing*/*erasure*. A stop-list prevents it merging unrelated
words like *access* and *process*.

## The known limits, stated deliberately

- The hashing embedder is **lexical, not semantic**. It will not bridge
  *customer* to *data subject*. That is why the run manifest records which
  embedding backend produced a report.
- The audit ledger is **tamper-evident, not tamper-proof**.
- The golden set is **10 hand-labelled cases** — enough to catch regressions,
  nowhere near enough to certify accuracy.
- NDMO control identifiers within a domain are **Muraqib's own** numbering, not
  official control numbers.

A tool whose first principle is "be able to say I don't know" has to apply that
to itself.

## Things I would do next

1. **Multi-tenant isolation.** Reports are in-process today; a shared
   deployment needs per-tenant storage with row-level isolation.
2. **Evidence ingestion.** Today evidence is declared as text. Reading it from
   the actual artefacts — a policy PDF, a Terraform plan, an IdP export — is
   where most of the remaining manual effort is.
3. **A larger, multi-labeller golden set**, with inter-annotator agreement
   reported. Ten cases labelled by one person is a starting point, not a
   benchmark.
4. **Anchoring the ledger head hash** somewhere external, which is what turns
   tamper-evident into tamper-resistant.
5. **Control-level drift detection** — instruments change (the EU AI Act
   deferral is a live example), so the corpus needs a review cadence and a
   changelog.
