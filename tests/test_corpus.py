"""Corpus integrity. These are the tests that stop a bad edit to a YAML file
silently degrading every assessment."""

import pytest

from muraqib.corpus import Corpus, CorpusError
from muraqib.models import Framework, Obligation


def test_all_frameworks_load(corpus):
    assert len(corpus.packs) == 8
    assert len(corpus.all_controls()) >= 100


def test_control_ids_are_globally_unique(corpus):
    ids = [c.id for c in corpus.all_controls()]
    assert len(ids) == len(set(ids))


def test_every_control_has_a_question_and_weight(corpus):
    for c in corpus.all_controls():
        assert c.question.strip(), f"{c.id} has no assessment question"
        assert c.question.endswith("?") or "?" in c.question, f"{c.id} question is not a question"
        assert 1 <= c.weight <= 5


def test_no_verbatim_regulatory_text_is_shipped(corpus):
    """Licence compliance: we redistribute no source text, only our own wording."""
    for c in corpus.all_controls():
        assert c.verbatim_text_included is False, f"{c.id} claims to include verbatim source text"


def test_every_pack_declares_source_and_licence(corpus):
    for pack in corpus.packs:
        assert pack.source_url.startswith("http"), f"{pack.framework} has no source URL"
        assert pack.licence_note, f"{pack.framework} has no licence note"
        assert pack.issuing_body


def test_non_binding_instruments_are_labelled_as_such(corpus):
    """SDAIA AI Ethics Principles and the NIST AI RMF are guidance, not law.
    Mislabelling them would be a factual error in every report."""
    assert corpus.pack(Framework.SDAIA_AI_ETHICS).obligation is Obligation.NON_BINDING_GUIDANCE
    assert corpus.pack(Framework.NIST_AI_RMF).obligation is Obligation.NON_BINDING_GUIDANCE
    assert corpus.pack(Framework.ISO_IEC_42001).obligation is Obligation.CERTIFIABLE_STANDARD
    assert corpus.pack(Framework.GDPR).obligation is Obligation.BINDING_LAW
    assert corpus.pack(Framework.EU_AI_ACT).obligation is Obligation.BINDING_LAW


def test_eu_ai_act_status_note_reflects_the_2026_deferral(corpus):
    """Regulation (EU) 2026/1744 moved Annex III high-risk to 2 Dec 2027.
    A stale date here would be a credibility failure in front of a regulator."""
    note = corpus.pack(Framework.EU_AI_ACT).status_note
    assert "2027" in note and "2026/1744" in note


def test_ndmo_covers_all_fifteen_domains(corpus):
    domains = {c.domain for c in corpus.pack(Framework.NDMO).controls}
    assert len(domains) == 15


def test_missing_directory_raises(tmp_path):
    with pytest.raises(CorpusError):
        Corpus.load(tmp_path / "nope")
