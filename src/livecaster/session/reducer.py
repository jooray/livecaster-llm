"""The app owns the state: deterministic reduction of LLM output (SPEC §9.3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, get_args

from rapidfuzz import fuzz

from livecaster.config import LLMConfig
from livecaster.llm.schemas import PreflightResult, TickResult
from livecaster.log import get_logger
from livecaster.outline.model import Outline, normalize
from livecaster.session.models import (
    CurrentTopic,
    Evidence,
    HotInfo,
    HotInfoRef,
    Mention,
    MentionKind,
    NewTopic,
    NodeState,
    Patch,
    Preflight,
    PreflightNode,
    Question,
    Session,
)

log = get_logger(__name__)

HOT_DECAY = 0.5
HOT_DROP_BELOW = 0.2
MAX_HOT = 5
MAX_QUESTIONS = 5
QUESTION_DEDUPE_RATIO = 85
NEW_TOPIC_DEDUPE_RATIO = 80
MENTION_KINDS = set(get_args(MentionKind))


@dataclass
class ManualAction:
    kind: Literal["mark", "pin", "unpin", "select"]
    node_id: str
    status: str | None = None
    pinned: bool | None = None


def _expandable(outline: Outline, node_id: str) -> bool:
    """May "this whole heading is done" be applied to every leaf underneath it?

    Only for a heading whose descendants are all leaves. Models occasionally claim
    the document title is covered, which would otherwise strike the entire outline
    through in one tick.
    """
    descendants = outline.descendants_of(node_id)
    if not descendants:
        return False
    return not any(d.kind == "heading" for d in descendants)


def ensure_state(session: Session, node_id: str) -> NodeState:
    st = session.nodes.get(node_id)
    if st is None:
        st = NodeState(id=node_id)
        session.nodes[node_id] = st
    return st


def mention_key(text: str) -> str:
    return normalize(text)


def apply_tick(
    session: Session,
    result: TickResult,
    now: float,
    outline: Outline,
    cfg: LLMConfig,
    *,
    new_part_start_t: float | None = None,
    window_start_t: float | None = None,
    language_locked: bool = False,
) -> Patch:
    """Apply one tick result. Rules 1..11 of SPEC §9.3, in order."""
    changed: dict[str, NodeState] = {}
    floor = 0.0 if window_start_t is None else max(0.0, window_start_t)

    def evidence_time(t: float) -> float:
        """Models sometimes answer `t: 1.0` for something said two minutes in.

        The evidence can only come from the transcript window that was sent, so
        clamp into it rather than stamping the outline with a nonsense time.
        """
        if not t:
            return now
        return min(max(t, floor), now)

    def touch(node_id: str) -> NodeState:
        st = ensure_state(session, node_id)
        changed[node_id] = st
        return st

    def locked(st: NodeState) -> bool:
        return st.manual_lock_until is not None and st.manual_lock_until > now

    # Rule 3 — covered
    for entry in result.covered:
        node = outline.get(entry.id)
        if node is None:
            log.debug("tick: unknown node id %s in covered", entry.id)
            continue
        targets = []
        if node.coverable:
            targets = [node]
        elif node.kind == "heading":
            if entry.confidence >= cfg.heading_cover_threshold and _expandable(outline, node.id):
                targets = outline.coverable_leaves_under(node.id)
            else:
                continue
        for target in targets:
            st = ensure_state(session, target.id)
            if locked(st) or st.status in ("covered", "skipped"):
                continue
            if entry.confidence >= cfg.cover_threshold:
                t = evidence_time(entry.t)
                st.status = "covered"
                st.covered_at = t
                st.evidence.append(Evidence(t=t, quote=entry.evidence, source="llm"))
                st.hot = None
                st.pinned = False
                changed[target.id] = st
            elif entry.confidence >= cfg.touch_threshold and st.status == "untouched":
                st.status = "touched"
                st.note = entry.evidence[:160] or st.note
                changed[target.id] = st

    # Rule 4 — touched
    for entry in result.touched:
        node = outline.get(entry.id)
        if node is None or not node.coverable:
            if node is not None and node.kind == "heading":
                continue
            log.debug("tick: unknown node id %s in touched", entry.id)
            continue
        st = ensure_state(session, entry.id)
        if locked(st):
            continue
        if st.status == "untouched":
            st.status = "touched"
            st.note = entry.note or st.note
            changed[entry.id] = st

    # Rule 5 — hot
    new_hot: dict[str, HotInfo] = {}
    for entry in result.hot:
        node = outline.get(entry.id)
        if node is None or not node.coverable:
            continue
        st = ensure_state(session, entry.id)
        if st.status in ("covered", "skipped"):
            continue
        new_hot[entry.id] = HotInfo(
            score=max(0.0, min(1.0, entry.score)),
            label=entry.label,
            reason=entry.reason,
            segue=entry.segue,
            since_t=st.hot.since_t if st.hot else now,
        )
    for node_id, st in session.nodes.items():
        if st.hot is None or node_id in new_hot:
            continue
        if st.pinned:
            new_hot[node_id] = st.hot
            continue
        decayed = st.hot.score * HOT_DECAY
        if decayed < HOT_DROP_BELOW:
            st.hot = None
            touch(node_id)
        else:
            new_hot[node_id] = HotInfo(
                score=decayed,
                label=st.hot.label,
                reason=st.hot.reason,
                segue=st.hot.segue,
                since_t=st.hot.since_t,
            )
    for node_id, st in session.nodes.items():
        if st.pinned and st.status not in ("covered", "skipped"):
            prev = new_hot.get(node_id) or st.hot or HotInfo(since_t=now)
            new_hot[node_id] = HotInfo(
                score=1.0,
                label=prev.label,
                reason=prev.reason,
                segue=prev.segue,
                since_t=prev.since_t,
            )

    ranked = sorted(new_hot.items(), key=lambda kv: -kv[1].score)[:MAX_HOT]
    kept = {node_id for node_id, _ in ranked}
    for node_id, st in session.nodes.items():
        if st.hot is not None and node_id not in kept:
            st.hot = None
            touch(node_id)
    next_refs: list[HotInfoRef] = []
    for rank, (node_id, hot) in enumerate(ranked, start=1):
        hot.rank = rank
        st = touch(node_id)
        st.hot = hot
        next_refs.append(
            HotInfoRef(
                node_id=node_id,
                score=hot.score,
                label=hot.label,
                reason=hot.reason,
                segue=hot.segue,
                rank=rank,
            )
        )

    # Rule 6 — questions
    previous = {normalize(q.text): q for q in session.suggestions.questions}
    questions: list[Question] = []
    for i, q in enumerate(result.questions[:MAX_QUESTIONS]):
        key = normalize(q.text)
        first_seen = now
        for old_key, old in previous.items():
            if fuzz.ratio(key, old_key) >= QUESTION_DEDUPE_RATIO:
                first_seen = old.first_seen
                break
        questions.append(
            Question(
                id=f"Q{session.usage.ticks}_{i}",
                text=q.text,
                node_id=q.node_id if q.node_id and q.node_id in outline else None,
                why=q.why,
                first_seen=first_seen,
            )
        )
    session.suggestions.questions = questions

    # Rule 7 — mentions
    by_key = {mention_key(m.text): m for m in session.mentions}
    for m in result.mentions:
        key = mention_key(m.text)
        if not key:
            continue
        existing = by_key.get(key)
        first_t = new_part_start_t if new_part_start_t is not None else now
        if existing is None:
            mention = Mention(
                id=f"M{len(session.mentions) + 1}",
                kind=m.kind if m.kind in MENTION_KINDS else "other",  # type: ignore[arg-type]
                text=m.text,
                context=m.context,
                first_t=first_t,
                url=m.url,
                needs_link=bool(m.needs_link and not m.url),
                search_query=m.search_query,
            )
            session.mentions.append(mention)
            by_key[key] = mention
        else:
            existing.first_t = min(existing.first_t, first_t)
            if len(m.context) > len(existing.context):
                existing.context = m.context
            if existing.url is None and m.url:
                existing.url = m.url
            if existing.search_query is None and m.search_query:
                existing.search_query = m.search_query
            existing.needs_link = bool(existing.needs_link and not existing.url)

    # Rule 8 — new topics
    for t in result.new_topics:
        key = normalize(t.title)
        existing_titles = [normalize(x.title) for x in session.suggestions.new_topics]
        if any(fuzz.ratio(key, other) >= NEW_TOPIC_DEDUPE_RATIO for other in existing_titles):
            continue
        session.suggestions.new_topics.append(
            NewTopic(
                id=f"N{len(session.suggestions.new_topics) + 1}",
                title=t.title,
                summary=t.summary,
                since_t=new_part_start_t if new_part_start_t is not None else now,
            )
        )

    # Rule 9 — language
    if result.language:
        code = result.language.lower()[:5]
        session.language_votes[code] = session.language_votes.get(code, 0) + 1
        if not language_locked:
            session.language = max(session.language_votes.items(), key=lambda kv: kv[1])[0]

    # Rule 10 — current topic
    current_id = result.current.node_id if result.current.node_id in outline else None
    session.suggestions.current = CurrentTopic(node_id=current_id, summary=result.current.summary)
    session.suggestions.next = next_refs

    return Patch(
        nodes=dict(changed),
        suggestions=session.suggestions,
        mentions=list(session.mentions),
        usage=session.usage,
        language=session.language,
    )


def apply_manual(session: Session, action: ManualAction, now: float, cfg: LLMConfig) -> Patch:
    """Manual overrides (FR-20)."""
    st = ensure_state(session, action.node_id)
    if action.kind == "mark":
        target = action.status or "untouched"
        if target == "covered":
            st.status = "covered"
            st.covered_at = now
            st.hot = None
            st.pinned = False
            st.evidence.append(Evidence(t=now, quote="", source="manual"))
            st.manual_lock_until = None
        elif target == "skipped":
            st.status = "skipped"
            st.hot = None
            st.pinned = False
            st.manual_lock_until = None
        else:
            st.status = "untouched"
            st.covered_at = None
            # An uncover is a correction: keep the LLM out for a while (FR-20).
            st.manual_lock_until = now + cfg.manual_lock_minutes * 60.0
    elif action.kind == "pin":
        st.pinned = True
        # Rule 5 keeps covered and skipped nodes out of the hot list; do not light one
        # up here either, or the UI shows a hot bar until the next tick removes it.
        if st.status not in ("covered", "skipped"):
            if st.hot is None:
                st.hot = HotInfo(score=1.0, since_t=now)
            else:
                st.hot.score = 1.0
        _rerank_hot(session)
    elif action.kind == "unpin":
        st.pinned = False
        if st.hot is not None:
            st.hot.score = min(st.hot.score, 0.6)
        _rerank_hot(session)
    return Patch(nodes={action.node_id: st}, suggestions=session.suggestions)


def _rerank_hot(session: Session) -> None:
    hot = [(node_id, s.hot) for node_id, s in session.nodes.items() if s.hot is not None]
    hot.sort(key=lambda kv: -kv[1].score)  # type: ignore[union-attr]
    refs: list[HotInfoRef] = []
    for rank, (node_id, info) in enumerate(hot[:MAX_HOT], start=1):
        assert info is not None
        info.rank = rank
        refs.append(
            HotInfoRef(
                node_id=node_id,
                score=info.score,
                label=info.label,
                reason=info.reason,
                segue=info.segue,
                rank=rank,
            )
        )
    for _node_id, info in hot[MAX_HOT:]:
        if info is not None:
            info.rank = None
    session.suggestions.next = refs


def apply_preflight(session: Session, result: PreflightResult, outline: Outline) -> Preflight:
    """Store pre-flight questions/triggers, dropping IDs the outline does not have."""
    nodes: dict[str, PreflightNode] = {}
    for n in result.nodes:
        if n.id not in outline:
            log.debug("preflight: unknown node id %s", n.id)
            continue
        nodes[n.id] = PreflightNode(
            id=n.id,
            questions=list(n.questions),
            triggers=list(n.triggers),
            related=[r for r in n.related if r in outline],
        )
    pf = Preflight(language=result.language, nodes=nodes)
    session.preflight = pf
    if result.language and not session.language:
        session.language = result.language.lower()[:5]
    return pf


def apply_warm(session: Session, scores: dict[str, float], now: float) -> Patch:
    """Fast-lane warmth (FR-16). Never changes a status."""
    changed: dict[str, NodeState] = {}
    for node_id, score in scores.items():
        st = ensure_state(session, node_id)
        if score > st.warm:
            st.warm = score
            st.warm_at = now
            changed[node_id] = st
    return Patch(nodes=changed)


def decay_warm(session: Session, now: float, per_minute: float = 0.2) -> Patch:
    """20 % decay per minute (SPEC §7.6)."""
    changed: dict[str, NodeState] = {}
    for node_id, st in session.nodes.items():
        if st.warm <= 0.0:
            continue
        minutes = max(0.0, (now - st.warm_at) / 60.0)
        if minutes <= 0:
            continue
        new = st.warm * (1.0 - per_minute) ** minutes
        if new < 0.05:
            new = 0.0
        if abs(new - st.warm) > 1e-6:
            st.warm = new
            st.warm_at = now
            changed[node_id] = st
    return Patch(nodes=changed)


def coverage_summary(session: Session, outline: Outline) -> dict[str, Any]:
    leaves = outline.leaf_ids()
    covered = sum(1 for i in leaves if session.nodes.get(i) and session.nodes[i].status == "covered")
    skipped = sum(1 for i in leaves if session.nodes.get(i) and session.nodes[i].status == "skipped")
    touched = sum(1 for i in leaves if session.nodes.get(i) and session.nodes[i].status == "touched")
    return {"total": len(leaves), "covered": covered, "touched": touched, "skipped": skipped}
