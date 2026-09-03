"""Filler and hallucination filters (SPEC §7.3, M4)."""

from __future__ import annotations

import pytest

from livecaster.stt.postprocess import clean, has_repetition, is_filler_only, is_hallucination


@pytest.mark.parametrize("text", ["hm", "Mhm.", "ehm ehm", "uh, um", "  Hmm  "])
def test_filler_only_segments_are_dropped(text):
    assert is_filler_only(text)
    assert clean(text) == ""


@pytest.mark.parametrize(
    "text",
    [
        "Titulky vytvořil JohnDoe",
        "Subtitles by the Amara.org community",
        "Thanks for watching!",
        "[Hudba]",
        "Ďakujem za pozretie",
    ],
)
def test_known_hallucinations_are_dropped(text):
    assert is_hallucination(text)
    assert clean(text) == ""


def test_repetition_is_a_hallucination():
    assert has_repetition("ano ano ano")
    assert has_repetition("dobre teda dobre teda dobre teda")
    assert not has_repetition("ano ano")
    assert not has_repetition("dych je dobrý nástroj na upokojenie")


def test_real_text_survives_and_whitespace_collapses():
    assert clean("  Predĺžený   výdych\n aktivuje parasympatikus.  ") == (
        "Predĺžený výdych aktivuje parasympatikus."
    )


def test_empty_input():
    assert clean("") == ""
    assert clean(None) == ""  # type: ignore[arg-type]


def test_filler_word_inside_a_sentence_is_kept():
    assert clean("No hej, ale ten výdych funguje") == "No hej, ale ten výdych funguje"
