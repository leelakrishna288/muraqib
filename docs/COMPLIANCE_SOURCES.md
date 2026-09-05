# Compliance sources, legal status and licensing

Every instrument Muraqib assesses against, with its exact official name, its
issuing body, whether it is actually binding, where the authoritative text
lives, and what Muraqib does and does not redistribute.

Getting a name or a status wrong here is a credibility failure in front of the
only audience that matters, so each entry is stated precisely.

Verified 2026-09-05.

---

## 1. What Muraqib ships, and what it does not

**Muraqib ships no verbatim regulatory text.** For every instrument it ships:

- the official control identifier or clause number (a factual reference),
- the domain / chapter taxonomy (also factual),
- **its own paraphrased assessment question**, written by this project,
- a link to the authoritative source.

Reasons, in order of importance:

1. **Licensing.** The NDMO standards and SDAIA's AI Ethics Principles are
   published free of charge but carry **no explicit redistribution grant**.
   Free-to-download is not free-to-redistribute. ISO/IEC 42001 is explicitly
   copyrighted and sold by ISO. Shipping their text would be a licensing risk
   this project does not need to take.
2. **Correctness.** A forked copy of a regulation goes stale silently. A
   reference-and-link architecture cannot.
3. **Honesty.** A paraphrase clearly labelled as a paraphrase is more useful to
   an assessor than a partial quotation that reads as authoritative.

Enforced by `tests/test_corpus.py::test_no_verbatim_regulatory_text_is_shipped`.

GDPR, the EU AI Act and the NIST AI RMF *are* freely reusable (EUR-Lex reuse
policy; US Government work). Muraqib still paraphrases them, for consistency of
voice across the corpus.

---

## 2. Saudi Arabia

### NDMO — National Data Management and Personal Data Protection Standards

- **Body:** National Data Management Office, operating under the Saudi Data & AI
  Authority (SDAIA). Note the expansion: *Office*, not *Organization* — the
  latter is a common error.
- **Structure:** 15 domains, 77 controls, 191 compliance specifications.
- **Status:** mandatory for government entities in the Kingdom; widely adopted as
  the de-facto benchmark by regulated private-sector organisations.
- **Source:** https://sdaia.gov.sa/ndmo/Files/PoliciesEn001.pdf
- **Muraqib coverage:** 40 assessment items spanning all 15 domains.
- **Identifier note:** Muraqib control ids take the form `NDMO.<DOMAIN>.<n>`.
  **The domain taxonomy is the standard's; the numbering within a domain is
  Muraqib's own.** These are not official NDMO control numbers and must not be
  cited as such.

The 15 domains: Data Governance · Data Catalog and Metadata · Data Quality ·
Data Operations · Document and Content Management · Data Architecture and
Modelling · Reference and Master Data Management · Business Intelligence and
Analytics · Data Sharing and Interoperability · Data Value Realization ·
Open Data · Freedom of Information · Data Classification · Personal Data
Protection · Data Security and Protection.

### SDAIA AI Ethics Principles (Version 2.0)

- **Body:** SDAIA. **Status: NON-BINDING GUIDANCE.** v1.0 September 2023.
- **Seven principles:** Fairness · Privacy & Security · Humanity · Social and
  Environmental Benefits · Reliability & Safety · Transparency &
  Explainability · Accountability & Responsibility. Plus four risk tiers.
- **Source:** https://sdaia.gov.sa/en/SDAIA/about/Documents/ai-principles.pdf
- **Muraqib coverage:** 17 assessment items.

> **Saudi Arabia has no binding horizontal AI law as of September 2026.**
> Governance is policy-based and led by SDAIA; enforcement teeth come from the
> PDPL, not from AI-specific legislation. Describing these principles as "Saudi
> AI law" is wrong. The draft **AI Hub Law** (public consultation April 2025)
> concerns data embassies and sovereign hosting, not AI system regulation, and
> is unenacted.

Related non-binding SDAIA material: Generative AI Guidelines for Government,
Generative AI Guidelines for the Public, AI Adoption Framework, Deepfakes
Guidelines.

### Personal Data Protection Law (PDPL)

- **Full citation:** Royal Decree No. M/19 of 9/2/1443H (16 September 2021),
  amended by Royal Decree No. M/148 of 5/9/1444H (27 March 2023).
- **Regulator:** SDAIA. **Status: in force** — effective 14 September 2023,
  **full enforcement from 14 September 2024**.
- **Accompanied by:** the Implementing Regulations and the Regulations on
  Personal Data Transfer outside the Kingdom.
- **Muraqib coverage:** 10 thematic assessment items. Control ids are Muraqib's
  own thematic groupings, **not statutory article numbers**.

---

## 3. United Arab Emirates

### Federal Decree-Law No. 45 of 2021 on the Protection of Personal Data

- **Regulator:** UAE Data Office, established under Federal Decree-Law No. 44 of
  2021.
- **Status:** issued 26 September 2021, effective 2 January 2022. **The
  Executive Regulations have still not been issued as of September 2026**, so
  several enforcement mechanisms remain incomplete.
- **Source:** https://u.ae/en/about-the-uae/digital-uae/data/data-protection-laws
- **Muraqib coverage:** 7 thematic assessment items.

### Other UAE instruments (referenced, not assessed as controls)

- **UAE Charter for the Development and Use of Artificial Intelligence**
  (June 2024, 12 principles) — **non-binding**.
- **UAE National Strategy for Artificial Intelligence 2031** (October 2017) —
  strategy, non-binding.
- **AI Ethics Guide** (UAE AI Office, December 2022) — non-binding.
- **Artificial Intelligence and Advanced Technology Council (AIATC)**,
  Abu Dhabi, established by Law No. 3 of 2024 — binding establishment law.

> **The UAE has no binding horizontal AI law as of September 2026.**

**Free zones run separate regimes.** An entity in DIFC is subject to the DIFC
Data Protection Law (DIFC Law No. 5 of 2020); ADGM has its own Data Protection
Regulations. Muraqib's UAE pack targets the **federal** regime — check which
applies before relying on the output.

---

## 4. European Union

### Regulation (EU) 2024/1689 — the AI Act

In force since 1 August 2024. **Amended by the Digital Omnibus, Regulation
(EU) 2026/1744, in force 27 July 2026** — formally adopted and published, not a
proposal.

| Obligation | Applies from |
|---|---|
| Prohibitions (Art. 5) + AI literacy (Art. 4) | 2 February 2025 |
| GPAI model obligations (Arts. 51–56) | 2 August 2025 |
| Transparency (Art. 50(2)) + NCII/CSAM prohibitions | 2 December 2026 |
| **High-risk — Annex III (Art. 6(2))** | **2 December 2027** *(moved from 2 Aug 2026)* |
| **High-risk — Annex I (Art. 6(1))** | **2 August 2028** *(moved from 2 Aug 2027)* |
| Public-authority deployers | 2 August 2030 |

> Any material stating that Annex III high-risk obligations apply from
> 2 August 2026 is **out of date**. This deferral is exactly the kind of detail
> an interviewer or a client will use to check whether you have read the source.

Source: https://eur-lex.europa.eu/eli/reg/2024/1689/oj
Muraqib coverage: 13 assessment items across Arts. 5, 6, 9–15, 50, 51–56, 72, 73.

### Regulation (EU) 2016/679 — GDPR

Applicable since 25 May 2018.
Source: https://eur-lex.europa.eu/eli/reg/2016/679/oj/eng
Muraqib coverage: 14 assessment items across Arts. 5, 6, 9, 13–14, 15–22, 25,
28, 30, 32, 33–34, 35, 44–49.

---

## 5. Standards and voluntary frameworks

### NIST AI 100-1 — AI Risk Management Framework 1.0

- **Official designation:** NIST AI 100-1. Released 26 January 2023.
  **Voluntary.**
- **Structure:** four functions — GOVERN, MAP, MEASURE, MANAGE.
- **Companions:** AI RMF Playbook; **NIST AI 600-1**, Generative AI Profile
  (26 July 2024).
- **Licence:** US Government work, not subject to copyright in the US. Freely
  reusable.
- **Source:** https://www.nist.gov/itl/ai-risk-management-framework
- **Muraqib coverage:** 12 assessment items.

### ISO/IEC 42001:2023

- **Full title:** *Information technology — Artificial intelligence —
  Management system.* Published 18 December 2023 by ISO/IEC JTC 1/SC 42.
  Certifiable AI management system (AIMS) standard.
- **Licence:** **copyrighted and sold by ISO.** Muraqib references **clause
  numbers only** and reproduces no text. Purchase the standard for the
  authoritative wording.
- **Source:** https://www.iso.org/standard/42001
- **Muraqib coverage:** 8 assessment items across clauses 4–10.

---

## 6. If you want to ship the verbatim text

You would need written permission. Practical route:

1. Email SDAIA / NDMO describing the tool and asking for redistribution
   permission for the control text. Getting a written answer is also a good
   thing to be able to mention in an interview.
2. ISO/IEC 42001 text cannot be redistributed at all — reference clause numbers
   and direct users to purchase.
3. Never claim the tool "certifies" or "guarantees" compliance with any of
   these. Muraqib's disclaimer is in every report and in the MCP server's
   `initialize` instructions for exactly this reason.

## 7. Retrieval caveat

The two SDAIA PDFs above returned HTTP 403 to automated fetching during
research (WAF / proxy layer). The URLs are corroborated by multiple independent
secondary sources, but the documents themselves were **not retrieved
programmatically**. Download them in a normal browser before relying on any
specific control wording.
