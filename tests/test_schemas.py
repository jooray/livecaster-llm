"""Strict-mode JSON Schema invariants (M2)."""

from __future__ import annotations

from typing import Any

import pytest

from livecaster.llm.schemas import (
    FINAL_SCHEMA,
    LINKS_SCHEMA,
    PREFLIGHT_SCHEMA,
    SCHEMAS,
    TICK_SCHEMA,
    FinalAnalysis,
    response_format,
)

ALL = [TICK_SCHEMA, PREFLIGHT_SCHEMA, FINAL_SCHEMA, LINKS_SCHEMA]


def walk(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


@pytest.mark.parametrize("schema", ALL)
def test_every_object_forbids_extra_properties(schema):
    for obj in walk(schema):
        if obj.get("type") == "object":
            assert obj.get("additionalProperties") is False


@pytest.mark.parametrize("schema", ALL)
def test_every_property_is_required(schema):
    for obj in walk(schema):
        if obj.get("type") == "object":
            assert set(obj.get("required", [])) == set(obj.get("properties", {}))


def test_response_format_shape():
    rf = response_format("tick")
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["name"] == "tick"
    assert rf["json_schema"]["schema"] is TICK_SCHEMA


@pytest.mark.parametrize("kind", sorted(SCHEMAS))
def test_models_accept_their_own_empty_defaults(kind):
    _, model = SCHEMAS[kind]
    model()


def test_final_analysis_label_falls_back_to_english():
    analysis = FinalAnalysis(labels={"summary": "Zhrnutie"})
    assert analysis.label("summary") == "Zhrnutie"
    assert analysis.label("chapters") == "Chapters"
    assert analysis.label("mentions") == "Mentions and links"


def test_final_analysis_fixture_validates(fixtures):
    import json

    data = json.loads((fixtures / "final_analysis.json").read_text(encoding="utf-8"))
    analysis = FinalAnalysis.model_validate(data)
    assert analysis.language == "sk"
    assert len(analysis.titles) >= 8
    assert len(analysis.description_short) <= 300
    assert len(analysis.description_long) <= 1500
    assert 5 <= len(analysis.chapters) <= 15
