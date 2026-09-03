"""Fast lane trigger matching (FR-16, M3)."""

from __future__ import annotations

import json

from livecaster.session.fastlane import FastLane, fold, significant_words
from livecaster.session.models import Preflight, PreflightNode, Segment


def _preflight(fixtures) -> Preflight:
    data = json.loads((fixtures / "preflight.json").read_text(encoding="utf-8"))
    return Preflight(
        language=data["language"],
        nodes={n["id"]: PreflightNode(**n) for n in data["nodes"]},
    )


def test_diacritics_insensitive_matching(outline, fixtures):
    lane = FastLane(outline, _preflight(fixtures))
    scores = lane.score_text("Mala som klientov, ktori prisli po ayahuasce a psychedelika ich rozhodili")
    assert "T29" in scores
    assert scores["T29"] >= 0.5


def test_wim_hof_warms_the_right_item(outline, fixtures):
    lane = FastLane(outline, _preflight(fixtures))
    scores = lane.score_text("Wim Hof je iná intenzita ako holotropné dýchanie")
    assert "T28" in scores


def test_filler_sentence_warms_nothing(outline, fixtures):
    lane = FastLane(outline, _preflight(fixtures))
    assert lane.score_text("No hej, presne tak, mhm.") == {}
    assert lane.score_text("") == {}


def test_scores_never_exceed_one(outline, fixtures):
    lane = FastLane(outline, _preflight(fixtures))
    for score in lane.score_text("buteyko co2 retencia zádrž dychu oxid uhličitý breath hold").values():
        assert 0.0 < score <= 1.0


def test_works_without_preflight(outline):
    lane = FastLane(outline)
    scores = lane.score_text("Hovorili sme o psychedelikách a o ayahuaske v porovnaní s dychom")
    assert isinstance(scores, dict)
    assert lane.phrases, "node words alone must give the lane something to match"


def test_score_segment_uses_the_text(outline, fixtures):
    lane = FastLane(outline, _preflight(fixtures))
    segment = Segment(id="S1", channel="Guest", t0=0, t1=2, text="ayahuasca a psilocybín")
    assert "T29" in lane.score_segment(segment)


def test_significant_words_drops_short_words_and_stopwords():
    words = significant_words("Preco to vlastne funguje, ked dychanie nie je ezoterika?")
    assert "preco" not in words and "vlastne" not in words
    assert "dychanie" in words and "ezoterika" in words


def test_fold_removes_diacritics():
    assert fold("Psychedeliká") == "psychedelika"
