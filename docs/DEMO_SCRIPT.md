# Two-minute live demo

Everything here runs offline on the deterministic engine: no API key, no network,
no cost. Every output shown was produced by running the commands on commit
`c100101`. If a number here disagrees with what your terminal prints, trust the
terminal and fix this file.

## Setup (once, before anyone is watching)

```bash
git clone https://github.com/leelakrishna288/muraqib && cd muraqib
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Have a second terminal open at the repo root. Practise once — the demo is short
enough that hesitation is the only thing that can spoil it.

---

## 0 · The one sentence, before you type anything

> "It takes a description of an AI system and tells you what it can prove about
> that system's compliance, what it can't, and — the part most tools skip —
> what it could not assess at all."

Then start typing. Do not explain the architecture first; show the verdict move.

---

## 1 · What it assesses against  (15 seconds)

```bash
muraqib frameworks
```

**Expect:** a table of 8 instruments, `Total controls: 121`.

**Say:** *"Eight instruments — the Saudi NDMO standards, SDAIA's AI ethics
principles, both PDPLs, the EU AI Act, GDPR, NIST's AI RMF and ISO 42001. Four
of those are binding law. No regulatory text is redistributed — the control
questions are my paraphrase mapped to the official domain taxonomy, and every
report says so."*

---

## 2 · The demonstration that matters  (45 seconds)

```bash
muraqib assess examples/aldar_tenant_assistant.yaml
```

**Expect:** `coverage 14.9% · assurance 5.2% · production assurance BLOCKED (39 blockers)`

**Say:** *"A fictional tenant-services assistant, as its team actually described
it. Blocked, with 39 assurance blockers."*

```bash
muraqib assess examples/tenant_assistant_remediated.yaml
```

**Expect:** `coverage 41.3% · assurance 39.7% · production assurance PERMITTED`

**Say — this is the line to land:**

> *"Same platform. Same engine. Nothing in the code changed between those two
> runs — only the evidence did. That's the whole point. A governance tool is
> worth nothing unless its verdict moves when the evidence moves, and moves for
> the right reason."*

**If they ask why coverage is only 41%:** because the remediated config still
doesn't evidence most controls, and unevidenced is reported as `not_assessable`
rather than guessed at. Sparse input, sparse assessment — that is the intended
behaviour, not a bug.

---

## 3 · Governing a live request  (40 seconds)

```bash
muraqib govern examples/transaction_blocked.yaml --policy examples/governance_policy.yaml
```

**Expect:** `BLOCK`, stopped at `source_authorization`, every later gate `not_run`.

**Say:** *"Six fail-closed gates on every live request. This one was
authenticated, it was entitled to the document-search tool, and it still
stopped — because the document that came back belonged to HR, not to the person
asking. Source-level permission is not object-level permission. And notice
everything downstream says `not_run`, not `pass`. The journey stopped; those
checks did not happen, and the record says so."*

If you have another thirty seconds, run one more:

```bash
muraqib govern examples/transaction_injection.yaml --policy examples/governance_policy.yaml
```

**Say:** *"The prompt here is completely benign. The attack is inside a
retrieved SharePoint excerpt — the text the user never saw and didn't write. A
gate that scans only the prompt reports 'no injection detected' and passes it
straight through. That was a real defect in this codebase, found by running it,
and it's shipped as an example now so it can't quietly come back."*

---

## 4 · The audit trail  (20 seconds)

```bash
muraqib verify-ledger data/runs/<run-id>.ledger.jsonl
```

**Expect:** `VERIFIED - verified N entries`.

Then, if you want the strongest thirty seconds in the demo, edit one value in
one entry of a copy — touching no hash field — and run it again:

**Expect:** `TAMPERED - entry 11 has been modified`, exit code 1.

**Say:** *"Hash-chained. Change one value in one entry and verification fails.
Tamper-evident, not tamper-proof — I can't stop someone deleting the file. What
I can stop is someone editing it quietly."*

---

## 5 · If they want to see the engineering  (30 seconds)

```bash
muraqib evaluate
```

**Expect:** 18 cases, status accuracy 100%, **over-claim rate 0%**, abstention 100%.

**Say:** *"Over-claim is the metric that matters. A false 'compliant' is a client
shipping a system they think is safe. It's weighted asymmetrically on purpose
and CI fails the build at anything above zero. This harness has already reverted
one of my own fixes — I added base-form verbs to the assessor, 'logs' matched the
noun in 'prompt logs carry no classification labels', and over-claim went from 0
to 16.7% inside one run. That's the harness doing exactly the job it was built
for."*

```bash
pip install -e ".[graph]"
MURAQIB_ORCHESTRATOR=langgraph pytest tests/test_langgraph_backend.py -v
```

**Say:** *"The same six agents run on a hand-rolled orchestrator or on a compiled
LangGraph StateGraph, and CI asserts the two produce identical findings across
all 121 controls. Not because LangGraph is better — because the contract is the
graph, not the library, and until you have two implementations you can't tell
which behaviour belongs to which."*

---

## Questions you should expect, and honest answers

**"Is this real compliance advice?"**
No. It's a gap analysis. It will not tell you that you comply with anything and
it's built so that it can't. It's a working aid for the people who then do the
real review.

**"Did an LLM write the control corpus?"**
The 15-domain NDMO taxonomy is the standard's. The assessment items inside each
domain are mine, written against the published domain descriptions, and the
identifiers are Muraqib's own — not official NDMO control numbers. That's stated
in the README, in `docs/COMPLIANCE_SOURCES.md`, and in every report the tool
produces.

**"The offline assessor is just keyword matching."**
Yes, and deliberately. It's a deterministic, zero-cost regression baseline so the
evaluation harness has a fixed floor. It abstains where a language model would
judge. Point the router at a real provider and the same pipeline runs against it
— the engine that produced a report is recorded in the run manifest, so a report
never misrepresents how it was made.

**"Are those real companies?"**
No. Both example platforms are fictional and the repository says so in several
places. Never imply otherwise.

**"What would you do differently with a team?"**
Worth having a real answer ready. Honest ones: the golden set needs a second
labeller, because right now the person who wrote the engine also wrote its
answer key; and the runtime plane needs to be wired to a real identity provider
and data catalog before its verdicts mean anything outside a demo.
