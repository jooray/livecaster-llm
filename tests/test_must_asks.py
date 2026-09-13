"""Bold lines the host cannot afford to miss (FR-40).

The rule has to separate two things real outlines both write in bold: a label the
host addressed to themselves, and a bold run used to title a sub-item.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from livecaster.outline.parser import is_must_ask, parse_outline, parse_outline_file
from livecaster.outline.remap import reindex_new_nodes, remap
from livecaster.session.exports import missed_must_asks
from livecaster.session.models import NodeState, Session

REPO = Path(__file__).resolve().parents[1]


# --- the rule itself --------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "**Question to answer on camera:** why is this not just Whisper?",
        "**Closing question:** what would I want it to do that it does not do yet?",
        "**Otázka na záver:** čo by som chcel, aby to vedelo?",
        "**Otázka:** Vníma to ako budovanie paralelných priestorov?",
        "__Question:__ does the guest see it that way?",
        "**Do not leave without asking about the licence**",
        "  **Ask this one:** really   ",
    ],
)
def test_bold_label_or_whole_line_is_a_must_ask(text):
    assert is_must_ask(text)


@pytest.mark.parametrize(
    "text",
    [
        # A bold run titling a sub-item, then an em-dash. osnova.md has three.
        "**Psychedeliká vs. dych** — ako to vníma v porovnaní s dychom?",
        "**Rozdiel medzi jej prácou a holotropným dýchaním** — intenzita, riziká",
        "**Psychedelics vs. breath** — how does the guest compare them?",
        # Bold that is not at the start is emphasis, not a marker.
        "Ask about the **licence** at some point",
        "plain text with no bold at all",
        "",
        "   ",
        # A colon outside the bold run is ordinary prose.
        "**Breath work** and why: it matters",
    ],
)
def test_bold_titles_and_emphasis_are_not_must_asks(text):
    assert not is_must_ask(text)


# --- against the real outlines ---------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("demo/demo.md", 2),
        ("demo/demo-sk.md", 2),
        ("tests/fixtures/osnova.md", 1),
        ("tests/fixtures/outline_en.md", 1),
    ],
)
def test_real_outlines_have_the_must_asks_the_host_wrote(name, expected):
    outline = parse_outline_file(REPO / name)
    assert sum(1 for n in outline.nodes if n.must) == expected


def test_front_matter_labels_are_never_must_asks():
    """`**Format:**` before the first heading is front matter, not a question."""
    outline = parse_outline_file(REPO / "demo/demo.md")
    front = [n for n in outline.nodes if n.kind == "meta"]
    assert front, "the demo outline opens with bold front matter"
    assert not any(n.must for n in front)


def test_a_must_ask_is_always_coverable():
    outline = parse_outline_file(REPO / "demo/demo.md")
    assert all(n.coverable for n in outline.nodes if n.must)


# --- surviving a reload -----------------------------------------------------


def test_must_ask_keeps_its_state_across_an_outline_reload():
    """FR-04 remap plus FR-40: editing elsewhere must not reset a marked question."""
    before = "## One\n\n- ordinary item\n\n**Closing question:** what did we miss?\n"
    after = "## One\n\n- ordinary item\n- a new bullet\n\n**Closing question:** what did we miss?\n"
    old = parse_outline(before)
    must_old = next(n for n in old.nodes if n.must)
    states = {must_old.id: NodeState(id=must_old.id, status="covered", covered_at=12.0)}

    new = parse_outline(after, start_id=old.next_id)
    _, retired, id_map = remap(old, new, states)
    reindex_new_nodes(new, id_map, old.next_id)

    # Reindexing hands matched nodes their old IDs back, which is why the engine
    # keeps everything that was not retired rather than the remapped dict.
    survivors = {k: v for k, v in states.items() if k not in retired}
    must_new = next(n for n in new.nodes if n.must)
    assert must_new.id == must_old.id
    assert survivors[must_new.id].status == "covered"
    assert survivors[must_new.id].covered_at == 12.0


def test_a_line_that_stops_being_bold_stops_being_a_must_ask():
    old = parse_outline("## One\n\n**Closing question:** what did we miss?\n")
    new = parse_outline("## One\n\nClosing question: what did we miss?\n")
    assert any(n.must for n in old.nodes)
    assert not any(n.must for n in new.nodes)


# --- the wrap-up ------------------------------------------------------------


def _session_with(outline, statuses: dict[str, str]) -> Session:
    session = Session(id="s", outline=outline.nodes)
    for node_id, status in statuses.items():
        session.nodes[node_id] = NodeState(id=node_id, status=status)  # type: ignore[arg-type]
    return session


def test_uncovered_must_ask_reaches_the_show_notes():
    outline = parse_outline_file(REPO / "demo/demo.md")
    must = [n for n in outline.nodes if n.must]
    missed = missed_must_asks(_session_with(outline, {}), outline)
    assert len(missed) == len(must)
    assert all(m["touched"] is False for m in missed)


def test_covered_and_skipped_must_asks_are_not_reported():
    outline = parse_outline_file(REPO / "demo/demo.md")
    a, b = (n.id for n in outline.nodes if n.must)
    assert missed_must_asks(_session_with(outline, {a: "covered", b: "skipped"}), outline) == []


def test_a_touched_must_ask_is_reported_as_touched():
    """Touched means it came up and was not answered — worth saying, not hiding."""
    outline = parse_outline_file(REPO / "demo/demo.md")
    a, b = (n.id for n in outline.nodes if n.must)
    missed = missed_must_asks(_session_with(outline, {a: "covered", b: "touched"}), outline)
    assert [m["touched"] for m in missed] == [True]


def test_an_outline_with_no_must_asks_reports_nothing():
    outline = parse_outline("## One\n\n- just a bullet\n")
    assert missed_must_asks(_session_with(outline, {}), outline) == []
