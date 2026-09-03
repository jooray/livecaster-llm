"""ID stability across outline edits (FR-04, M1)."""

from __future__ import annotations

from pathlib import Path

from livecaster.outline.parser import parse_outline, parse_outline_file
from livecaster.outline.remap import reindex_new_nodes, remap
from livecaster.session.models import NodeState


def _edit(text: str, marker: str, insert: str) -> str:
    lines = text.split("\n")
    i = next(n for n, line in enumerate(lines) if line.startswith(marker))
    lines.insert(i, insert)
    return "\n".join(lines)


def test_inserting_a_bullet_keeps_every_other_id(osnova_path: Path):
    old = parse_outline_file(osnova_path)
    text = osnova_path.read_text(encoding="utf-8")
    new = parse_outline(_edit(text, "- CO2 tolerancia", "- Nový bod o dychu a spánku"))

    states = {"T5": NodeState(id="T5", status="covered"), "T15": NodeState(id="T15", status="touched")}
    _, retired, id_map = remap(old, new, states)
    next_id = reindex_new_nodes(new, id_map, old.next_id)

    assert retired == {}
    ids = {n.id: n.text for n in new.nodes}
    assert all(n.id in ids for n in old.nodes), "every old id survives"
    assert ids["T15"].startswith("CO2 tolerancia")
    fresh = [i for i in ids if i not in {n.id for n in old.nodes}]
    assert fresh == ["T53"]
    assert next_id == 54


def test_removed_node_is_retired_and_never_reuses_its_number(osnova_path: Path):
    old = parse_outline_file(osnova_path)
    text = osnova_path.read_text(encoding="utf-8")
    without = "\n".join(line for line in text.split("\n") if not line.startswith("- CO2 tolerancia"))
    new = parse_outline(without)

    states = {"T15": NodeState(id="T15", status="covered", covered_at=12.0)}
    _, retired, id_map = remap(old, new, states)
    next_id = reindex_new_nodes(new, id_map, old.next_id)

    assert "T15" in retired
    assert retired["T15"].status == "covered"
    assert "T15" not in {n.id for n in new.nodes}
    assert next_id == old.next_id  # nothing new was added


def test_reordering_keeps_ids():
    old = parse_outline("## H\n\n- alpha\n- beta\n- gamma\n")
    new = parse_outline("## H\n\n- gamma\n- alpha\n- beta\n")
    states = {n.id: NodeState(id=n.id, status="covered") for n in old.leaves()}
    _, retired, id_map = remap(old, new, states)
    reindex_new_nodes(new, id_map, old.next_id)
    assert retired == {}
    by_id = {n.id: n.text for n in new.nodes}
    assert by_id["T2"] == "alpha" and by_id["T4"] == "gamma"
    assert [n.text for n in new.leaves()] == ["gamma", "alpha", "beta"]


def test_duplicate_text_maps_one_to_one():
    old = parse_outline("- same\n- same\n")
    new = parse_outline("- same\n- same\n")
    states = {"T1": NodeState(id="T1", status="covered"), "T2": NodeState(id="T2", status="skipped")}
    out, retired, _ = remap(old, new, states)
    assert retired == {}
    assert out["T1"].status == "covered" and out["T2"].status == "skipped"
