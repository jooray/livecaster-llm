"""Every rule of SPEC §9.3 (M2)."""

from __future__ import annotations

import pytest

from livecaster.config import LLMConfig
from livecaster.llm.schemas import (
    PreflightNodeResult,
    PreflightResult,
    TickCovered,
    TickCurrent,
    TickHot,
    TickMention,
    TickNewTopic,
    TickQuestion,
    TickResult,
    TickTouched,
)
from livecaster.session.models import HotInfo, NodeState, Session
from livecaster.session.reducer import (
    ManualAction,
    apply_manual,
    apply_preflight,
    apply_tick,
    apply_warm,
    decay_warm,
)


@pytest.fixture
def session(outline) -> Session:
    return Session(id="s", outline=outline.nodes, outline_next_id=outline.next_id)


@pytest.fixture
def cfg() -> LLMConfig:
    return LLMConfig()


def tick(**kw) -> TickResult:
    kw.setdefault("current", TickCurrent())
    return TickResult(**kw)


# --- rule 1 ---------------------------------------------------------------


def test_unknown_ids_are_ignored(session, outline, cfg):
    result = tick(
        covered=[TickCovered(id="T999", confidence=1.0, evidence="x", t=1.0)],
        touched=[TickTouched(id="nope", note="x")],
        hot=[TickHot(id="T404", score=1.0, reason="r", segue="s")],
    )
    patch = apply_tick(session, result, 10.0, outline, cfg)
    assert "T999" not in session.nodes
    assert patch.nodes == {}


# --- rule 2 ---------------------------------------------------------------


def test_manual_lock_blocks_the_llm(session, outline, cfg):
    session.nodes["T5"] = NodeState(id="T5", manual_lock_until=600.0)
    result = tick(covered=[TickCovered(id="T5", confidence=0.99, evidence="e", t=100.0)])
    apply_tick(session, result, 100.0, outline, cfg)
    assert session.nodes["T5"].status == "untouched"
    # ...and stops blocking once it expires.
    apply_tick(session, result, 700.0, outline, cfg)
    assert session.nodes["T5"].status == "covered"


# --- rule 3 ---------------------------------------------------------------


def test_cover_and_touch_thresholds(session, outline, cfg):
    result = tick(
        covered=[
            TickCovered(id="T5", confidence=0.7, evidence="a", t=10.0),
            TickCovered(id="T6", confidence=0.5, evidence="b", t=11.0),
            TickCovered(id="T7", confidence=0.3, evidence="c", t=12.0),
        ]
    )
    apply_tick(session, result, 20.0, outline, cfg)
    assert session.nodes["T5"].status == "covered"
    assert session.nodes["T5"].covered_at == 10.0
    assert session.nodes["T5"].evidence[0].quote == "a"
    assert session.nodes["T6"].status == "touched"
    assert session.nodes.get("T7", NodeState(id="T7")).status == "untouched"


def test_evidence_time_is_clamped_to_the_transcript_window(session, outline, cfg):
    result = tick(
        covered=[
            TickCovered(id="T5", confidence=0.9, evidence="a", t=1.0),  # implausibly early
            TickCovered(id="T6", confidence=0.9, evidence="b", t=140.0),  # inside the window
            TickCovered(id="T7", confidence=0.9, evidence="c", t=9999.0),  # after now
            TickCovered(id="T9", confidence=0.9, evidence="d", t=0.0),  # not given
        ]
    )
    apply_tick(session, result, 200.0, outline, cfg, window_start_t=120.0)
    assert session.nodes["T5"].covered_at == 120.0
    assert session.nodes["T6"].covered_at == 140.0
    assert session.nodes["T7"].covered_at == 200.0
    assert session.nodes["T9"].covered_at == 200.0
    assert session.nodes["T5"].evidence[0].t == 120.0


def test_heading_covered_needs_high_confidence(session, outline, cfg):
    leaves = [n.id for n in outline.coverable_leaves_under("T10")]
    apply_tick(
        session, tick(covered=[TickCovered(id="T10", confidence=0.8, evidence="e", t=1)]), 1, outline, cfg
    )
    assert all(session.nodes.get(i, NodeState(id=i)).status == "untouched" for i in leaves)

    apply_tick(
        session, tick(covered=[TickCovered(id="T10", confidence=0.9, evidence="e", t=5)]), 5, outline, cfg
    )
    assert all(session.nodes[i].status == "covered" for i in leaves)


def test_the_document_title_never_covers_the_whole_outline(session, outline, cfg):
    """T1 owns every section; a model claiming it is covered must not strike everything."""
    root = outline.nodes[0]
    assert root.kind == "heading" and root.level == 1
    apply_tick(
        session, tick(covered=[TickCovered(id=root.id, confidence=1.0, evidence="e", t=5)]), 5, outline, cfg
    )
    assert not any(st.status == "covered" for st in session.nodes.values())


def test_a_heading_with_subheadings_is_not_expandable(session, outline, cfg):
    """`## 3.` has nested bullets but no sub-headings, so it still expands."""
    section = next(n for n in outline.nodes if n.text.startswith("3. Rituál"))
    apply_tick(
        session,
        tick(covered=[TickCovered(id=section.id, confidence=0.9, evidence="e", t=5)]),
        5,
        outline,
        cfg,
    )
    leaves = [n.id for n in outline.coverable_leaves_under(section.id)]
    assert leaves and all(session.nodes[i].status == "covered" for i in leaves)


def test_covered_is_never_downgraded_and_skipped_is_untouchable(session, outline, cfg):
    session.nodes["T5"] = NodeState(id="T5", status="covered", covered_at=3.0)
    session.nodes["T6"] = NodeState(id="T6", status="skipped")
    apply_tick(
        session,
        tick(touched=[TickTouched(id="T5", note="n"), TickTouched(id="T6", note="n")]),
        9,
        outline,
        cfg,
    )
    assert session.nodes["T5"].status == "covered" and session.nodes["T5"].covered_at == 3.0
    assert session.nodes["T6"].status == "skipped"


# --- rule 4 ---------------------------------------------------------------


def test_touched_only_upgrades_untouched(session, outline, cfg):
    apply_tick(session, tick(touched=[TickTouched(id="T5", note="passing mention")]), 4.0, outline, cfg)
    assert session.nodes["T5"].status == "touched"
    assert session.nodes["T5"].note == "passing mention"


# --- rule 5 ---------------------------------------------------------------


def test_hot_decay_and_drop(session, outline, cfg):
    apply_tick(session, tick(hot=[TickHot(id="T29", score=0.8, reason="r", segue="s")]), 1.0, outline, cfg)
    assert session.nodes["T29"].hot.score == pytest.approx(0.8)
    assert session.nodes["T29"].hot.rank == 1

    apply_tick(session, tick(), 2.0, outline, cfg)
    assert session.nodes["T29"].hot.score == pytest.approx(0.4)
    apply_tick(session, tick(), 3.0, outline, cfg)
    assert session.nodes["T29"].hot.score == pytest.approx(0.2)
    apply_tick(session, tick(), 4.0, outline, cfg)
    assert session.nodes["T29"].hot is None


def test_hot_is_capped_at_five_and_ranked(session, outline, cfg):
    ids = ["T5", "T6", "T7", "T11", "T12", "T13"]
    result = tick(hot=[TickHot(id=i, score=1.0 - n / 10, reason="r", segue="s") for n, i in enumerate(ids)])
    apply_tick(session, result, 1.0, outline, cfg)
    assert len(session.suggestions.next) == 5
    assert [r.node_id for r in session.suggestions.next] == ids[:5]
    assert [r.rank for r in session.suggestions.next] == [1, 2, 3, 4, 5]
    assert session.nodes["T13"].hot is None


def test_covered_nodes_never_go_hot(session, outline, cfg):
    session.nodes["T5"] = NodeState(id="T5", status="covered")
    apply_tick(session, tick(hot=[TickHot(id="T5", score=1.0, reason="r", segue="s")]), 1.0, outline, cfg)
    assert session.nodes["T5"].hot is None


def test_pinned_nodes_stay_hot_at_one(session, outline, cfg):
    session.nodes["T29"] = NodeState(id="T29", pinned=True, hot=HotInfo(score=0.3, reason="r"))
    apply_tick(session, tick(), 1.0, outline, cfg)
    assert session.nodes["T29"].hot.score == 1.0
    apply_tick(session, tick(), 2.0, outline, cfg)
    assert session.nodes["T29"].hot.score == 1.0


# --- rule 6 ---------------------------------------------------------------


def test_questions_replace_but_keep_first_seen(session, outline, cfg):
    q = TickQuestion(text="Ako spoznáš, že je to koreňový problém?", node_id="T24", why="w")
    apply_tick(session, tick(questions=[q]), 10.0, outline, cfg)
    assert session.suggestions.questions[0].first_seen == 10.0

    near = TickQuestion(text="Ako spoznas, ze je to korenovy problem?", node_id="T24", why="w2")
    apply_tick(session, tick(questions=[near]), 40.0, outline, cfg)
    assert len(session.suggestions.questions) == 1
    assert session.suggestions.questions[0].first_seen == 10.0

    apply_tick(
        session, tick(questions=[TickQuestion(text="Something else entirely?", why="w")]), 60.0, outline, cfg
    )
    assert session.suggestions.questions[0].first_seen == 60.0


def test_questions_are_capped_at_five(session, outline, cfg):
    qs = [TickQuestion(text=f"Question number {i}?", why="w") for i in range(8)]
    apply_tick(session, tick(questions=qs), 1.0, outline, cfg)
    assert len(session.suggestions.questions) == 5


# --- rule 7 ---------------------------------------------------------------


def test_mentions_dedupe_and_merge(session, outline, cfg):
    first = TickMention(
        kind="person", text="Wim Hof", context="short", url=None, needs_link=True, search_query="q"
    )
    apply_tick(session, tick(mentions=[first]), 30.0, outline, cfg, new_part_start_t=25.0)
    assert len(session.mentions) == 1
    assert session.mentions[0].first_t == 25.0
    assert session.mentions[0].needs_link is True

    second = TickMention(
        kind="person",
        text="wim  hof!",
        context="a much longer context line",
        url="https://www.wimhofmethod.com",
        needs_link=True,
        search_query="q",
    )
    apply_tick(session, tick(mentions=[second]), 90.0, outline, cfg, new_part_start_t=80.0)
    assert len(session.mentions) == 1
    m = session.mentions[0]
    assert m.first_t == 25.0
    assert m.context == "a much longer context line"
    assert m.url == "https://www.wimhofmethod.com"
    assert m.needs_link is False


def test_mention_with_a_url_is_not_flagged(session, outline, cfg):
    m = TickMention(
        kind="link", text="dychova-praca.example", context="", url="https://dychova-praca.example", needs_link=True, search_query=None
    )
    apply_tick(session, tick(mentions=[m]), 1.0, outline, cfg)
    assert session.mentions[0].needs_link is False


# --- rule 8 ---------------------------------------------------------------


def test_new_topics_dedupe_fuzzily(session, outline, cfg):
    apply_tick(
        session, tick(new_topics=[TickNewTopic(title="Neurofeedback", summary="a")]), 5.0, outline, cfg
    )
    apply_tick(
        session, tick(new_topics=[TickNewTopic(title="Neurofeedback.", summary="b")]), 9.0, outline, cfg
    )
    assert len(session.suggestions.new_topics) == 1
    apply_tick(
        session, tick(new_topics=[TickNewTopic(title="Studená voda", summary="c")]), 12.0, outline, cfg
    )
    assert len(session.suggestions.new_topics) == 2


# --- rule 9 ---------------------------------------------------------------


def test_language_is_a_majority_vote(session, outline, cfg):
    apply_tick(session, tick(language="sk"), 1.0, outline, cfg)
    apply_tick(session, tick(language="cs"), 2.0, outline, cfg)
    apply_tick(session, tick(language="sk"), 3.0, outline, cfg)
    assert session.language_votes == {"sk": 2, "cs": 1}
    assert session.language == "sk"


def test_configured_language_is_not_overridden(session, outline, cfg):
    session.language = "en"
    apply_tick(session, tick(language="sk"), 1.0, outline, cfg, language_locked=True)
    assert session.language == "en"
    assert session.language_votes == {"sk": 1}


# --- rule 10 --------------------------------------------------------------


def test_current_topic_is_replaced_and_validated(session, outline, cfg):
    apply_tick(session, tick(current=TickCurrent(node_id="T15", summary="CO2")), 1.0, outline, cfg)
    assert session.suggestions.current.node_id == "T15"
    apply_tick(session, tick(current=TickCurrent(node_id="T999", summary="?")), 2.0, outline, cfg)
    assert session.suggestions.current.node_id is None
    assert session.suggestions.current.summary == "?"


# --- manual actions (FR-20) -----------------------------------------------


def test_uncover_locks_out_the_llm(session, outline, cfg):
    session.nodes["T5"] = NodeState(id="T5", status="covered", covered_at=10.0)
    apply_manual(session, ManualAction(kind="mark", node_id="T5", status="untouched"), 100.0, cfg)
    st = session.nodes["T5"]
    assert st.status == "untouched" and st.covered_at is None
    assert st.manual_lock_until == 100.0 + cfg.manual_lock_minutes * 60

    apply_tick(
        session,
        tick(covered=[TickCovered(id="T5", confidence=1.0, evidence="e", t=110)]),
        110.0,
        outline,
        cfg,
    )
    assert session.nodes["T5"].status == "untouched"


def test_manual_cover_and_skip(session, outline, cfg):
    apply_manual(session, ManualAction(kind="mark", node_id="T6", status="covered"), 42.0, cfg)
    assert session.nodes["T6"].status == "covered"
    assert session.nodes["T6"].covered_at == 42.0
    assert session.nodes["T6"].evidence[0].source == "manual"

    apply_manual(session, ManualAction(kind="mark", node_id="T7", status="skipped"), 43.0, cfg)
    assert session.nodes["T7"].status == "skipped"


def test_pinning_a_skipped_node_does_not_light_it_up(session, outline, cfg):
    session.nodes["T29"] = NodeState(id="T29", status="skipped")
    apply_manual(session, ManualAction(kind="pin", node_id="T29"), 5.0, cfg)
    assert session.nodes["T29"].pinned is True
    assert session.nodes["T29"].hot is None
    assert session.suggestions.next == []


def test_pin_and_unpin(session, outline, cfg):
    apply_manual(session, ManualAction(kind="pin", node_id="T29"), 5.0, cfg)
    assert session.nodes["T29"].pinned is True
    assert session.nodes["T29"].hot.score == 1.0
    assert session.suggestions.next[0].node_id == "T29"

    apply_manual(session, ManualAction(kind="unpin", node_id="T29"), 6.0, cfg)
    assert session.nodes["T29"].pinned is False
    assert session.nodes["T29"].hot.score <= 0.6


# --- warm (FR-16) ---------------------------------------------------------


def test_warm_never_changes_status_and_decays(session, outline, cfg):
    apply_warm(session, {"T29": 0.8}, 0.0)
    assert session.nodes["T29"].warm == 0.8
    assert session.nodes["T29"].status == "untouched"
    decay_warm(session, 60.0)
    assert session.nodes["T29"].warm == pytest.approx(0.64)
    decay_warm(session, 60.0 + 60 * 20)
    assert session.nodes["T29"].warm == 0.0


# --- preflight ------------------------------------------------------------


def test_preflight_drops_unknown_ids(session, outline):
    result = PreflightResult(
        language="sk",
        nodes=[
            PreflightNodeResult(id="T5", questions=["q"], triggers=["session"], related=["T43", "T999"]),
            PreflightNodeResult(id="T999", questions=["q"], triggers=["x"], related=[]),
        ],
    )
    pf = apply_preflight(session, result, outline)
    assert set(pf.nodes) == {"T5"}
    assert pf.nodes["T5"].related == ["T43"]
    assert session.language == "sk"
