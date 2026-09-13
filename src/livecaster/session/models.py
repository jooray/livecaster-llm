"""Session state models (SPEC §7.4)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from livecaster.config import ChannelConfig
from livecaster.outline.model import Node

Status = Literal["untouched", "touched", "covered", "skipped"]
MentionKind = Literal[
    "person", "book", "article", "link", "tool", "product", "place", "event", "concept", "promise", "other"
]


class Word(BaseModel):
    start: float
    end: float
    text: str


class Evidence(BaseModel):
    t: float
    quote: str
    segment_ids: list[str] = Field(default_factory=list)
    source: Literal["llm", "manual"] = "llm"


class HotInfo(BaseModel):
    score: float = 0.0
    label: str = ""
    reason: str = ""
    segue: str = ""
    since_t: float = 0.0
    rank: int | None = None


class NodeState(BaseModel):
    id: str
    status: Status = "untouched"
    covered_at: float | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    hot: HotInfo | None = None
    warm: float = 0.0
    warm_at: float = 0.0
    pinned: bool = False
    manual_lock_until: float | None = None
    note: str | None = None


class Segment(BaseModel):
    id: str
    channel: str
    speaker: str | None = None
    t0: float
    t1: float
    text: str
    language: str | None = None
    words: list[Word] = Field(default_factory=list)
    engine: str = ""

    def word_count(self) -> int:
        return len(self.text.split())


class Mention(BaseModel):
    id: str
    kind: MentionKind = "other"
    text: str
    context: str = ""
    first_t: float = 0.0
    segment_ids: list[str] = Field(default_factory=list)
    url: str | None = None
    needs_link: bool = True
    search_query: str | None = None


class CurrentTopic(BaseModel):
    node_id: str | None = None
    summary: str = ""


class HotInfoRef(BaseModel):
    node_id: str
    score: float
    label: str = ""
    reason: str = ""
    segue: str = ""
    rank: int | None = None


class Question(BaseModel):
    id: str
    text: str
    node_id: str | None = None
    why: str = ""
    first_seen: float = 0.0


class NewTopic(BaseModel):
    id: str
    title: str
    summary: str = ""
    since_t: float = 0.0


class Suggestions(BaseModel):
    current: CurrentTopic | None = None
    next: list[HotInfoRef] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    new_topics: list[NewTopic] = Field(default_factory=list)


class PreflightNode(BaseModel):
    id: str
    questions: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)


class Preflight(BaseModel):
    language: str | None = None
    nodes: dict[str, PreflightNode] = Field(default_factory=dict)


class ModelUsage(BaseModel):
    calls: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class Usage(BaseModel):
    ticks: int = 0
    failures: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    by_model: dict[str, ModelUsage] = Field(default_factory=dict)

    def add(
        self,
        model: str,
        prompt_tokens: int,
        cached_tokens: int,
        completion_tokens: int,
        cost: float,
    ) -> None:
        self.prompt_tokens += prompt_tokens
        self.cached_tokens += cached_tokens
        self.completion_tokens += completion_tokens
        self.cost_usd += cost
        m = self.by_model.setdefault(model, ModelUsage())
        m.calls += 1
        m.prompt_tokens += prompt_tokens
        m.cached_tokens += cached_tokens
        m.completion_tokens += completion_tokens
        m.cost_usd += cost


class Session(BaseModel):
    id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    outline_path: str = ""
    mode: Literal["live", "remote", "replay"] = "live"
    language: str | None = None
    language_votes: dict[str, int] = Field(default_factory=dict)
    channels: list[ChannelConfig] = Field(default_factory=list)
    sync_marks: list[float] = Field(default_factory=list)
    outline: list[Node] = Field(default_factory=list)
    outline_next_id: int = 1
    retired_nodes: dict[str, NodeState] = Field(default_factory=dict)
    nodes: dict[str, NodeState] = Field(default_factory=dict)
    suggestions: Suggestions = Field(default_factory=Suggestions)
    mentions: list[Mention] = Field(default_factory=list)
    preflight: Preflight | None = None
    usage: Usage = Field(default_factory=Usage)
    status: Literal["idle", "running", "paused", "finishing", "finished"] = "idle"
    duration_s: float = 0.0
    #: How long this episode is meant to run, in minutes (FR-41). None means no
    #: budget was set, and the UI must not invent one.
    target_minutes: float | None = None
    final_paths: dict[str, str] = Field(default_factory=dict)

    @property
    def sync_offset(self) -> float:
        return self.sync_marks[-1] if self.sync_marks else 0.0


class Patch(BaseModel):
    """Incremental update broadcast to the UI (PLAN §5.4)."""

    nodes: dict[str, NodeState] = Field(default_factory=dict)
    suggestions: Suggestions | None = None
    mentions: list[Mention] | None = None
    usage: Usage | None = None
    language: str | None = None
    session_status: str | None = None

    def is_empty(self) -> bool:
        return not (
            self.nodes
            or self.suggestions
            or self.mentions is not None
            or self.usage
            or self.language
            or self.session_status
        )

    def merge(self, other: Patch) -> Patch:
        self.nodes.update(other.nodes)
        if other.suggestions is not None:
            self.suggestions = other.suggestions
        if other.mentions is not None:
            self.mentions = other.mentions
        if other.usage is not None:
            self.usage = other.usage
        if other.language is not None:
            self.language = other.language
        if other.session_status is not None:
            self.session_status = other.session_status
        return self


class Event(BaseModel):
    t: float
    kind: str
    data: dict[str, Any] = Field(default_factory=dict)
