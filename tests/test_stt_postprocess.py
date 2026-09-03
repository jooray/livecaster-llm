"""Filler and hallucination filters (SPEC §7.3, M4)."""

from __future__ import annotations

import pytest

from livecaster.stt.postprocess import (
    clean,
    has_repetition,
    is_filler_only,
    is_hallucination,
    script_of,
    wrong_script,
)


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


def test_mock_engine_describes_what_it_heard():
    import numpy as np

    from livecaster.stt.mock import MockEngine

    engine = MockEngine()
    engine.warmup()
    first = engine.transcribe(np.zeros(32_000, dtype=np.float32), None)
    assert first.text == "mock utterance 1 (2.0s)"
    assert engine.transcribe(np.zeros(16_000, dtype=np.float32), None).text == "mock utterance 2 (1.0s)"

    scripted = MockEngine(["prvá veta", "druhá veta"])
    assert scripted.transcribe(np.zeros(10, dtype=np.float32), None).text == "prvá veta"
    assert scripted.transcribe(np.zeros(10, dtype=np.float32), None).text == "druhá veta"
    assert scripted.transcribe(np.zeros(10, dtype=np.float32), None).text == "prvá veta"


# --- language guard (D20) ---------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Ну, мой брат за мною.",  # what Parakeet actually returned for Slovak speech
        "Всё хорошо, спасибо.",
    ],
)
def test_a_locked_latin_language_rejects_cyrillic(text):
    assert wrong_script(text, "sk")
    assert wrong_script(text, "cs")


@pytest.mark.parametrize(
    "text",
    [
        "Ako to celé robíš?",
        # Polish-looking Slovak is the drift this guard honestly cannot catch.
        "Teamow metoda jest taka troszkę naroczniejsza.",
    ],
)
def test_latin_text_survives_a_latin_lock(text):
    assert not wrong_script(text, "sk")


def test_cyrillic_survives_when_that_is_the_locked_language():
    assert not wrong_script("Ну, мой брат за мной.", "ru")


def test_nothing_is_rejected_while_the_language_is_open():
    assert not wrong_script("Ну, мой брат за мной.", None)
    assert not wrong_script("Ну, мой брат за мной.", "auto")


def test_too_few_letters_to_judge():
    assert script_of("да") is None
    assert not wrong_script("да", "sk")
