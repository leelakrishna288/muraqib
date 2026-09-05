# Architecture

## The shape of the problem

An AI-governance assessment is a mapping exercise with a hard constraint: the
cost of a false *compliant* is much higher than the cost of a false *unknown*.
Every design decision below follows from that asymmetry.

## Pipeline

```
PlatformConfig (untrusted)
        │
        ▼
┌───────────────────┐  injection scan (15 rules, severity-graded)
│ 1. IntakeAgent    │  PII redaction before anything reaches a model
│    deterministic  │  delimiter-spoof stripping
└───────────────────┘  → flattened, sanitised fact block
        │
        ▼
┌───────────────────┐  4 tiers from explicit rules
│ 2. RiskAgent      │  every rule that fired is recorded
│    deterministic  │  a model is never asked to guess the tier
└───────────────────┘
        │
        ▼
┌───────────────────┐  per control: hybrid BM25 + dense retrieval
│ 3. RetrievalAgent │  neighbours included so cross-framework overlap is visible
│    deterministic  │
└───────────────────┘
        │
        ▼
┌───────────────────┐  ── the first of two model-using nodes ──
│ 4. AssessorAgent  │  strict JSON schema  → violation = not_assessable
│    model          │  citation gate       → ungrounded = not_assessable
└───────────────────┘
        │
        ▼
┌───────────────────┐  reviews only optimistic findings on weight ≥ 4
│ 5. CriticAgent    │  or any "compliant" on a binding instrument
│    model          │  CAN ONLY DOWNGRADE
└───────────────────┘
        │
        ▼
┌───────────────────┐  coverage % and weighted % computed, never generated
│ 6. ReporterAgent  │  blocking gaps = weight-5 failures on binding law
│    deterministic  │  markdown / html / json / remediation plan
└───────────────────┘
        │
        ▼
   AssessmentReport + hash-chained audit ledger
```

Four of six nodes never touch a model. That ratio is the point.

## Why a graph and not an autonomous agent loop

The popular pattern is *model decides the next tool call, repeat until done*.
It is the wrong pattern here:

| Requirement | Autonomous loop | Explicit graph |
|---|---|---|
| Reproducibility | run twice, get two different traces | same input, same steps |
| Cost bound | unbounded by construction | node count is known before the run |
| Resumability | replay from the start | checkpoint after each node |
| Auditability | "the model decided to" | "node 4 ran, here is its input and output" |

An assessment that cannot be reproduced cannot be defended, and a defensible
audit trail is the actual deliverable.

The graph is a straight pipeline with declared preconditions per node — the same
explicit-state model that LangGraph and similar frameworks provide, implemented
directly so the state transitions are visible in the repository rather than
inside a dependency.

## State and resumability

`RunState` is one serialisable object; every node reads and writes it, and it is
checkpointed to `data/runs/<run_id>.state.json` after each node. `Orchestrator.resume`
skips nodes whose output is already present, and the assessor skips controls
already in `state.findings`. A run that dies at control 80 of 121 restarts at
80 — which matters when a provider rate-limits you three quarters of the way
through.

## The citation gate

The single most important component.

```
finding + retrieved_control_ids
        │
        ├── status is not_assessable / not_applicable ──▶ pass through
        │
        ├── has ≥1 citation whose source is "corpus"
        │   AND whose control_id exists in the corpus
        │   AND was retrieved for THIS control  ─────────▶ keep
        │
        └── otherwise ──▶ DOWNGRADE to not_assessable, record the rejection
```

**Downgrade, not drop.** Dropping an ungrounded finding would remove it from the
denominator, so the coverage percentage would *rise* as hallucination
increased — the tool would look better the more it made things up. Downgrading
makes hallucination visibly reduce the score. Incentives inside a metric matter
more than the metric's definition.

The gate also bounds prompt-injection damage: a successful injection can change
what the model *says*, but it cannot make the model cite a control that does not
exist in the loaded corpus, because the gate checks against the corpus and the
retrieval set, not against the model's claim.

## The critic's asymmetry

The critic can move `compliant → partial` and `partial → non_compliant`. It can
never move a finding the other way.

This is not timidity, it is where the risk lives. Nobody over-claims their way
into a false *non-compliant*: if the tool wrongly says a control fails, a human
looks at it, finds the evidence and corrects it — cost, one hour. If the tool
wrongly says a control passes, nobody looks at it again — cost, a regulatory
finding.

It reviews selectively (weight ≥ 4, or any *compliant* on a binding instrument)
because reviewing all 121 would roughly triple cost for no gain.

## Two numbers, never one

```
coverage_pct        = assessed / applicable          "how much did we reach a verdict on"
weighted_score_pct  = Σ(weight × score) / Σ(weight)  "how much of what matters is satisfied"
```

where `score` is 1.0 compliant, 0.5 partial, 0.0 otherwise.

A platform at 40% coverage and 95% weighted score has not been assessed; it has
been guessed at. Reporting only the second number is how compliance dashboards
end up lying, so both appear side by side at the top of every report, with a
plain-English gloss of what each one means.

## Retrieval

Hybrid, min-max normalised, fused as `α·dense + (1-α)·lexical` with α = 0.6.

BM25 is implemented directly (~60 lines, no dependency, fully testable) because
exact-term matching is load-bearing on this corpus: *Art. 22*, *cross-border
transfer*, *pgvector*, *DPIA*. Dense retrieval handles the paraphrases a client
actually writes.

Tokenisation applies light suffix stripping — enough to collapse
*erasing*/*erasure* and *classified*/*classification*, which measurably cost
recall, with an explicit stop-list for words where stripping would merge
unrelated concepts (*access*, *process*, *analysis*).

Two tests keep this honest: a 12-query labelled benchmark with a recall@5 floor,
and an assertion that fused retrieval is never worse than either half alone — if
the extra complexity stops paying for itself, CI says so.

## Provider abstraction

One method, `complete(messages) -> LLMResponse`. No streaming, no
model-initiated tool calls — the orchestrator decides what happens next, which
is what makes runs reproducible.

The default `offline` provider is a deterministic rule-based assessor. It exists
for three reasons, in order:

1. CI and the full test suite run with zero secrets, zero network and zero cost;
2. it is the **baseline** the eval harness scores every hosted model against, so
   the value an LLM adds is measured rather than assumed;
3. air-gapped and sovereign-cloud deployments can run the whole system with no
   external model — a real requirement in KSA and UAE public-sector work.

`ModelRouter` adds retry with jittered exponential backoff on retryable errors
only, and enforces two hard per-run ceilings: call count and estimated USD.
Unbounded autonomous model invocation is a named, unsolved cost-governance
problem in agent stacks; here it is bounded and the number appears in the report.

## Observability

- **Structured JSON logs**, with credential patterns scrubbed at the formatter.
- **Spans** for every node and model call, upgrading to OpenTelemetry when the
  extra is installed.
- **Hash-chained audit ledger** — append-only JSONL, each entry carrying the
  SHA-256 of its predecessor, with any field whose name looks like a credential
  redacted before it is written. `muraqib verify-ledger` walks the chain.

Tamper-**evident**, not tamper-proof; see the README.

## The runtime plane

Added after the assessment side, and deliberately separate: see
[`RUNTIME_GOVERNANCE.md`](RUNTIME_GOVERNANCE.md). It shares the audit ledger,
the injection scanner and the PII redactor with the assessor, and shares none of
its state. The assessor answers a question about a platform; the runtime plane
answers a question about a request.

The two are joined by one idea, and it is the same idea the citation gate
enforces: **"could not establish" is a distinct outcome from "failed"**. The
assessor calls it `not_assessable`; the runtime plane calls it `NOT_EVIDENCED`.
Both refuse to convert an absence of evidence into a pass, and both refuse to
convert it into a failure.

## Evidence maturity

A finding now carries how strong its evidence is, on a six-rung ladder from
runtime-verified down to none. Three numbers come out of an assessment where
there used to be two:

* **coverage** — of the applicable controls, how many reached a usable verdict;
* **weighted posture** — of the available control weight, how much is satisfied;
* **assurance** — the same, discounted by evidence maturity.

A platform can be 90% compliant on paper and 40% assured. That gap is the honest
finding, and before this existed the tool could not express it.

The **production assurance claim** is stricter still and deliberately binary: it
is permitted only when every *critical* control is satisfied **and** carries
production-grade evidence (runtime-verified or a verified configuration export).
Missing evidence is not a control failure — but it does prevent the claim, which
is a more precise statement than any percentage.

## Deliberate non-goals

- **No autonomous remediation.** The tool identifies gaps. Changing a client's
  production system is not something an agent should do unattended.
- **No certification claim.** Structurally impossible: the disclaimer is on the
  report model itself, and the MCP server repeats it in its `initialize`
  instructions.
- **No verbatim regulatory text.** Licensing plus staleness; see
  `COMPLIANCE_SOURCES.md`.
- **No credential handling in the API.** It is a resource server. Issuing
  identity belongs to the IdP.
