# The runtime governance plane

## Two different questions

Muraqib's assessment side answers **"is this platform governed?"** — once, from
a description, producing a report. That is a posture assessment, and it is what
most compliance tooling does.

The runtime plane answers a different question, on every request:
**"is *this transaction* permitted, right now?"**

They are not the same product, and one does not replace the other. A platform
can score well on posture and still leak a document, because posture is measured
against a description and a leak happens in a specific request. Equally, a
runtime plane that blocks correctly tells you nothing about whether your DPIA
exists. Muraqib does both and keeps them clearly separated.

## The gates

Six, in a fixed order, fail closed.

```
request ─▶ identity ─▶ entitlement ─▶ source auth ─▶ pre-inference ─▶ output DLP ─▶ release
```

The order is load-bearing:

- **identity before entitlement** — you cannot authorise a principal you have
  not established.
- **entitlement before source authorization** — a tool the user may not invoke
  should never reach the data layer to ask a question.
- **source authorization before inference** — the model must not be shown a
  document the user could not have opened. Once it is in the context window it
  is in the response, whatever you do afterwards.
- **inference before output protection** — you cannot DLP-scan a response that
  does not exist.
- **release last** — the decision, the response id and the provenance are
  recorded together or not at all.

### 1. Identity

Establishes the principal and whether the claim is strong enough to act on:
authenticated, MFA satisfied, channel approved, and **delegated (on-behalf-of)
identity present**.

That last one deserves its own note. When downstream systems are called with a
shared service principal rather than the user's own identity, every source-level
permission below becomes decorative — the data layer sees the application, not
the person, and returns whatever the application may see. It is one of the most
common ways an otherwise well-built AI platform ends up serving one user another
user's documents. So the gate blocks on it.

A principal carrying no roles or groups is `NOT_EVIDENCED`, not `BLOCK`: the
gate could not establish entitlement, which is a different statement from "this
principal is forbidden".

### 2. Entitlement

Which agents and tools this principal may invoke, resolved as the **union** of
every entitlement their roles and groups grant. Union, not intersection —
holding two roles grants what either grants.

Deny by default. A principal whose roles match nothing in the policy gets an
empty entitlement and can invoke nothing. `GovernancePolicy.default()` has no
entitlements at all, so an unconfigured deployment denies everything rather than
permitting everything, and there is a test asserting exactly that.

### 3. Source authorization

Two checks, and the second is the one that catches real incidents.

The first is straightforward: is each declared data source permitted for this
principal?

The second: **does each retrieved object actually belong to them?** A user may
legitimately have access to a SharePoint site while a specific document in it
belongs to HR. Source-level permission is not object-level permission, and a
retrieval layer that filters by source alone will hand that document to the
model, which will summarise it faithfully into the response.

Retrieved content with no classification label is `NOT_EVIDENCED` — handling
rules cannot be applied to data whose sensitivity is unknown.

### 4. Pre-inference

Everything that must be true before a prompt reaches a model:

- the model and provider are on the approved list;
- the processing region is approved — or, if `allow_cross_border` is on, the
  transaction is downgraded to `WARN` rather than blocked;
- **confidential or restricted context never crosses the border regardless**,
  because enabling cross-border processing is not a licence to move sensitive
  material. The classification check overrides the relaxation;
- the prompt has been minimised — unredacted personal data heading for a
  third-party model API is blocked, and a prompt merely *unmarked* as redacted
  is `NOT_EVIDENCED`;
- no high-severity prompt injection, reusing the same scanner the assessment
  side uses on untrusted platform configurations.

A model call with no declared region is `NOT_EVIDENCED`: residency cannot be
checked against a blank.

### 5. Output protection

DLP over the response, with four possible outcomes rather than two:

| Outcome | When |
|---|---|
| `ALLOW` | nothing sensitive detected |
| `MASK` | personal data present — masked in place and released |
| `BLOCK` | credential-shaped material, restricted content reproduced from retrieval, a blocked phrase, or a record count above the bulk-extraction threshold |

Masking rather than blocking on PII matters: a governance plane that blocks
every response containing an email address is a governance plane people route
around within a week.

### 6. Release

Produces the record an auditor asks for: response id, provenance (which sources
contributed), and the release decision itself.

## Fail-closed

The stopping rule lives in one place — `GovernanceEngine.evaluate` — rather than
being scattered through the gates:

- `BLOCK` **always** stops. Gates after it are recorded as `NOT_RUN`, so the
  trace shows what was and was not evaluated rather than leaving a silent gap.
- `NOT_EVIDENCED` stops when `policy.fail_closed` is true, which is the default.
  A gate that could not establish the fact it needed has not approved anything.
- `MASK` and `WARN` continue, but the transaction's final verdict carries the
  highest severity any gate reached — a masked response is never reported as a
  clean `ALLOW`.

Turning `fail_closed` off is a deliberate, recorded decision. It is never the
default, and the policy file says so in a comment above the setting.

## The audit trail

Every gate result — verdict, reasons, duration — is appended to the same
hash-chained ledger the assessment side uses. `muraqib verify-ledger` walks the
chain for a runtime decision exactly as it does for an assessment.

The principal is recorded as a salted digest (`principal:a1b2c3…`), never as an
email address. A test asserts the raw subject never appears anywhere in the
ledger, because an audit log that leaks the personal data it exists to protect
is its own incident.

The `trace()` method returns the reconstruction path:

```
principal=principal:c5acc5b46cd5 → session=TXN-9dac8e8f47bd → trace=f169680d…
  → identity=allow → entitlement=allow → source_authorization=allow
  → pre_inference=allow → output_protection=allow → release=allow
  → response=RSP-e0cc68bf58fb
```

## Policy as data

The policy is YAML, validated strictly with `extra="forbid"`. An unknown key is
an error rather than a silently ignored setting, because a rule that looks
enforced and does nothing is worse than no rule — it produces confidence without
control. There is a test for that too.

Every setting in the file is read by a gate. Nothing in it is advisory.

## What this is not

- **It does not discover your entitlements, classify your data, or verify the
  attributes it is given.** It decides on what the transaction declares. Wiring
  it to a real IdP, catalog and retrieval layer is the integration work, and
  until that is done the verdicts are only as good as the inputs.
- **It is not a certification.** It enforces a configured policy. That is a
  narrower and more useful claim than compliance.
- **It is not a proxy or a gateway.** It is a decision function. Where you call
  it from — middleware, an orchestrator, a sidecar — is your architecture's
  business, and keeping it a pure function is what makes it testable.
