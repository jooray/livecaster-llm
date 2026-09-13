"""Pydantic models for LLM output plus strict JSON Schemas (SPEC §9.2, §9.4, §9.5)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MENTION_KINDS = [
    "person",
    "book",
    "article",
    "link",
    "tool",
    "product",
    "place",
    "event",
    "concept",
    "promise",
    "other",
]


# --------------------------------------------------------------------------
# Tick
# --------------------------------------------------------------------------


class TickCurrent(BaseModel):
    node_id: str | None = None
    summary: str = ""


class TickCovered(BaseModel):
    id: str
    confidence: float = 0.0
    evidence: str = ""
    t: float = 0.0


class TickTouched(BaseModel):
    id: str
    note: str = ""


class TickHot(BaseModel):
    id: str
    score: float = 0.0
    label: str = ""
    reason: str = ""
    segue: str = ""


class TickQuestion(BaseModel):
    text: str
    node_id: str | None = None
    why: str = ""


class TickMention(BaseModel):
    kind: str = "other"
    text: str
    context: str = ""
    url: str | None = None
    needs_link: bool = True
    search_query: str | None = None


class TickNewTopic(BaseModel):
    title: str
    summary: str = ""


class TickResult(BaseModel):
    language: str = "en"
    current: TickCurrent = Field(default_factory=TickCurrent)
    covered: list[TickCovered] = Field(default_factory=list)
    touched: list[TickTouched] = Field(default_factory=list)
    hot: list[TickHot] = Field(default_factory=list)
    questions: list[TickQuestion] = Field(default_factory=list)
    mentions: list[TickMention] = Field(default_factory=list)
    new_topics: list[TickNewTopic] = Field(default_factory=list)


def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required if required is not None else list(props.keys()),
        "properties": props,
    }


def _arr(items: dict[str, Any], max_items: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "array", "items": items}
    if max_items is not None:
        out["maxItems"] = max_items
    return out


TICK_SCHEMA: dict[str, Any] = _obj(
    {
        "language": {
            "type": "string",
            "description": "ISO 639-1 code of the conversation language",
        },
        "current": _obj(
            {
                "node_id": {"type": ["string", "null"]},
                "summary": {"type": "string", "maxLength": 110},
            }
        ),
        "covered": _arr(
            _obj(
                {
                    "id": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence": {"type": "string", "maxLength": 240},
                    "t": {"type": "number"},
                }
            ),
            12,
        ),
        "touched": _arr(_obj({"id": {"type": "string"}, "note": {"type": "string", "maxLength": 160}}), 12),
        "hot": _arr(
            _obj(
                {
                    "id": {"type": "string"},
                    "score": {"type": "number", "minimum": 0, "maximum": 1},
                    "label": {"type": "string", "maxLength": 44},
                    "reason": {"type": "string", "maxLength": 90},
                    "segue": {"type": "string", "maxLength": 140},
                }
            ),
            5,
        ),
        "questions": _arr(
            _obj(
                {
                    "text": {"type": "string", "maxLength": 140},
                    "node_id": {"type": ["string", "null"]},
                    "why": {"type": "string", "maxLength": 80},
                }
            ),
            5,
        ),
        "mentions": _arr(
            _obj(
                {
                    "kind": {"type": "string", "enum": MENTION_KINDS},
                    "text": {"type": "string", "maxLength": 120},
                    "context": {"type": "string", "maxLength": 240},
                    "url": {"type": ["string", "null"]},
                    "needs_link": {"type": "boolean"},
                    "search_query": {"type": ["string", "null"]},
                }
            ),
            10,
        ),
        "new_topics": _arr(
            _obj(
                {
                    "title": {"type": "string", "maxLength": 120},
                    "summary": {"type": "string", "maxLength": 300},
                }
            ),
            3,
        ),
    }
)


# --------------------------------------------------------------------------
# Pre-flight
# --------------------------------------------------------------------------


class PreflightNodeResult(BaseModel):
    id: str
    questions: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)


class PreflightResult(BaseModel):
    language: str = "en"
    nodes: list[PreflightNodeResult] = Field(default_factory=list)


PREFLIGHT_SCHEMA: dict[str, Any] = _obj(
    {
        "language": {"type": "string"},
        "nodes": _arr(
            _obj(
                {
                    "id": {"type": "string"},
                    "questions": _arr({"type": "string", "maxLength": 240}, 3),
                    "triggers": _arr({"type": "string", "maxLength": 80}, 6),
                    "related": _arr({"type": "string"}, 5),
                }
            )
        ),
    }
)


# --------------------------------------------------------------------------
# Final analysis
# --------------------------------------------------------------------------

LABEL_KEYS = [
    "summary",
    "chapters",
    "covered",
    "uncovered",
    "missed_must",
    "titles",
    "description_short",
    "description_long",
    "social_post",
    "quotes",
    "mentions",
    "promises",
    "new_topics",
]

ENGLISH_LABELS = {
    "summary": "Summary",
    "chapters": "Chapters",
    "covered": "What we covered",
    "uncovered": "What we did not get to",
    "missed_must": "Marked, and never asked",
    "titles": "Title candidates",
    "description_short": "Short description",
    "description_long": "Long description",
    "social_post": "Social post",
    "quotes": "Key quotes",
    "mentions": "Mentions and links",
    "promises": "Promises made on air",
    "new_topics": "Topics outside the outline",
}


class Chapter(BaseModel):
    title: str
    start_t: float = 0.0
    end_t: float = 0.0
    node_ids: list[str] = Field(default_factory=list)


class CoveredNote(BaseModel):
    node_id: str
    note: str = ""


class TitleCandidate(BaseModel):
    text: str
    style: str = "descriptive"


class Quote(BaseModel):
    t: float = 0.0
    speaker: str | None = None
    text: str = ""


class Promise(BaseModel):
    t: float = 0.0
    text: str = ""


class FinalAnalysis(BaseModel):
    language: str = "en"
    labels: dict[str, str] = Field(default_factory=dict)
    summary: list[str] = Field(default_factory=list)
    chapters: list[Chapter] = Field(default_factory=list)
    covered: list[CoveredNote] = Field(default_factory=list)
    uncovered: list[CoveredNote] = Field(default_factory=list)
    titles: list[TitleCandidate] = Field(default_factory=list)
    description_short: str = ""
    description_long: str = ""
    #: One post, written to work as-is on X, Nostr, LinkedIn and Facebook. Per-platform
    #: variants and hashtags were dropped on the host's instruction (D23).
    social_post: str = ""
    quotes: list[Quote] = Field(default_factory=list)
    mentions: list[TickMention] = Field(default_factory=list)
    promises: list[Promise] = Field(default_factory=list)
    new_topics: list[TickNewTopic] = Field(default_factory=list)

    def label(self, key: str) -> str:
        return self.labels.get(key) or ENGLISH_LABELS.get(key, key.replace("_", " ").title())


FINAL_SCHEMA: dict[str, Any] = _obj(
    {
        "language": {"type": "string"},
        "labels": _obj({k: {"type": "string", "maxLength": 60} for k in LABEL_KEYS}),
        "summary": _arr({"type": "string", "maxLength": 400}, 8),
        "chapters": _arr(
            _obj(
                {
                    "title": {"type": "string", "maxLength": 90},
                    "start_t": {"type": "number"},
                    "end_t": {"type": "number"},
                    "node_ids": _arr({"type": "string"}, 8),
                }
            ),
            20,
        ),
        "covered": _arr(
            _obj({"node_id": {"type": "string"}, "note": {"type": "string", "maxLength": 240}}), 80
        ),
        "uncovered": _arr(
            _obj({"node_id": {"type": "string"}, "note": {"type": "string", "maxLength": 240}}), 80
        ),
        "titles": _arr(
            _obj(
                {
                    "text": {"type": "string", "maxLength": 120},
                    "style": {
                        "type": "string",
                        "enum": ["descriptive", "curiosity", "quote", "question", "short"],
                    },
                }
            ),
            12,
        ),
        "description_short": {"type": "string", "maxLength": 300},
        "description_long": {"type": "string", "maxLength": 1500},
        "social_post": {"type": "string", "maxLength": 1400},
        "quotes": _arr(
            _obj(
                {
                    "t": {"type": "number"},
                    "speaker": {"type": ["string", "null"]},
                    "text": {"type": "string", "maxLength": 400},
                }
            ),
            12,
        ),
        "mentions": _arr(
            _obj(
                {
                    "kind": {"type": "string", "enum": MENTION_KINDS},
                    "text": {"type": "string", "maxLength": 120},
                    "context": {"type": "string", "maxLength": 240},
                    "url": {"type": ["string", "null"]},
                    "needs_link": {"type": "boolean"},
                    "search_query": {"type": ["string", "null"]},
                }
            ),
            40,
        ),
        "promises": _arr(_obj({"t": {"type": "number"}, "text": {"type": "string", "maxLength": 240}}), 20),
        "new_topics": _arr(
            _obj(
                {
                    "title": {"type": "string", "maxLength": 120},
                    "summary": {"type": "string", "maxLength": 300},
                }
            ),
            10,
        ),
    }
)


# --------------------------------------------------------------------------
# Link resolution
# --------------------------------------------------------------------------


class ResolvedLink(BaseModel):
    text: str
    url: str | None = None


class LinkResolution(BaseModel):
    links: list[ResolvedLink] = Field(default_factory=list)


LINKS_SCHEMA: dict[str, Any] = _obj(
    {
        "links": _arr(
            _obj({"text": {"type": "string", "maxLength": 120}, "url": {"type": ["string", "null"]}}),
            40,
        )
    }
)


SCHEMAS: dict[str, tuple[dict[str, Any], type[BaseModel]]] = {
    "tick": (TICK_SCHEMA, TickResult),
    "preflight": (PREFLIGHT_SCHEMA, PreflightResult),
    "final": (FINAL_SCHEMA, FinalAnalysis),
    "links": (LINKS_SCHEMA, LinkResolution),
}


#: JSON Schema keywords the Anthropic structured-output validator rejects. Venice
#: forwards `response_format` straight through for its Claude models, so a schema
#: with `maxItems` comes back as
#: `output_config.format.schema: For 'array' type, property 'maxItems' is not supported`.
#: The array caps also live in the prompt and the reducer, so dropping them here
#: costs nothing but a few extra tokens when a model over-answers.
ANTHROPIC_UNSUPPORTED_KEYWORDS = frozenset({"maxItems", "minItems"})

#: Model families whose structured output goes through the Anthropic validator,
#: whether reached directly or proxied by Venice.
_ANTHROPIC_FAMILIES = ("claude", "opus", "sonnet", "haiku", "fable", "mythos")


def schema_dialect(model: str) -> Literal["anthropic", "openai"]:
    """Which JSON Schema subset a model id accepts."""
    name = model.rsplit(":", 1)[-1].casefold()
    return "anthropic" if any(f in name for f in _ANTHROPIC_FAMILIES) else "openai"


def _prune(node: Any, drop: frozenset[str]) -> Any:
    if isinstance(node, dict):
        return {k: _prune(v, drop) for k, v in node.items() if k not in drop}
    if isinstance(node, list):
        return [_prune(v, drop) for v in node]
    return node


def schema_for(name: str, dialect: str = "openai") -> dict[str, Any]:
    """The schema for one call kind, narrowed to what ``dialect`` accepts."""
    schema, _ = SCHEMAS[name]
    if dialect == "anthropic":
        return _prune(schema, ANTHROPIC_UNSUPPORTED_KEYWORDS)
    return schema


def response_format(
    name: Literal["tick", "preflight", "final", "links"], dialect: str = "openai"
) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": schema_for(name, dialect)},
    }
