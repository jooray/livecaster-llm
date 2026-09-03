"""Re-map node states after an outline reload (FR-04)."""

from __future__ import annotations

from livecaster.outline.model import Outline, normalize


def remap[S](
    old: Outline,
    new: Outline,
    states: dict[str, S],
) -> tuple[dict[str, S], dict[str, S], dict[str, str]]:
    """Carry states from ``old`` to ``new`` by normalized text.

    Returns ``(states_for_new, retired_states, id_map)``. Nodes whose text still
    exists keep their state; the rest are retired so the export can still show them.
    """
    old_by_norm: dict[str, list[str]] = {}
    for node in old.nodes:
        key = normalize(node.text)
        if not key:
            continue
        old_by_norm.setdefault(key, []).append(node.id)

    id_map: dict[str, str] = {}
    used_old: set[str] = set()
    for node in new.nodes:
        key = normalize(node.text)
        candidates = old_by_norm.get(key)
        if not candidates:
            continue
        for old_id in candidates:
            if old_id not in used_old:
                id_map[old_id] = node.id
                used_old.add(old_id)
                break

    out: dict[str, S] = {}
    retired: dict[str, S] = {}
    for old_id, state in states.items():
        new_id = id_map.get(old_id)
        if new_id is None:
            retired[old_id] = state
        else:
            out[new_id] = state
    return out, retired, id_map


def reindex_new_nodes(new: Outline, id_map: dict[str, str], next_id: int) -> int:
    """Give nodes that did not exist before fresh IDs, never reusing a retired number.

    Mutates ``new`` in place (ids, parent and children references) and returns the
    updated ``next_id`` counter.
    """
    mapped_targets = set(id_map.values())
    rename: dict[str, str] = {}
    for node in new.nodes:
        if node.id in mapped_targets:
            continue
        rename[node.id] = f"T{next_id}"
        next_id += 1

    # Nodes that were matched keep the OLD id, so build the full rename table.
    reverse = {v: k for k, v in id_map.items()}
    for node in new.nodes:
        if node.id in reverse:
            rename[node.id] = reverse[node.id]

    for node in new.nodes:
        node.id = rename.get(node.id, node.id)
        node.parent = rename.get(node.parent, node.parent) if node.parent else None
        node.children = [rename.get(c, c) for c in node.children]
    new.next_id = next_id
    new.reindex()
    return next_id
