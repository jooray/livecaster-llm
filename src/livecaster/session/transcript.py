"""Transcript buffer: append, window, new-since, cross-talk dedupe (FR-14, PLAN §5.3)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from rapidfuzz import fuzz

from livecaster.outline.model import normalize
from livecaster.session.models import Segment

CROSSTALK_SIMILARITY = 80.0


class Transcript:
    def __init__(self, direct_channels: Iterable[str] = ()) -> None:
        self.segments: list[Segment] = []
        self._by_id: dict[str, Segment] = {}
        self._arrival: dict[str, int] = {}
        self._counter = 0
        self.direct_channels = set(direct_channels)
        self.dropped_crosstalk = 0

    # --- ids ---------------------------------------------------------------

    def next_id(self) -> str:
        self._counter += 1
        return f"S{self._counter}"

    def adopt_counter(self) -> None:
        """Resume: continue numbering after the highest existing id."""
        best = 0
        for s in self.segments:
            if s.id.startswith("S") and s.id[1:].isdigit():
                best = max(best, int(s.id[1:]))
        self._counter = max(self._counter, best)

    # --- mutation ----------------------------------------------------------

    def append(self, segment: Segment) -> Segment | None:
        """Append a segment. Returns None when it was dropped as cross-talk (FR-14)."""
        victim = self._crosstalk_victim(segment)
        if victim is segment:
            self.dropped_crosstalk += 1
            return None
        if victim is not None:
            self._remove(victim)
            self.dropped_crosstalk += 1
        self.segments.append(segment)
        self._arrival[segment.id] = len(self._arrival)
        self.segments.sort(key=lambda s: (s.t0, self._arrival.get(s.id, 0)))
        self._by_id[segment.id] = segment
        return segment

    def _remove(self, segment: Segment) -> None:
        self.segments = [s for s in self.segments if s.id != segment.id]
        self._by_id.pop(segment.id, None)
        self._arrival.pop(segment.id, None)

    def _crosstalk_victim(self, incoming: Segment) -> Segment | None:
        """Which of the two near-identical overlapping segments should go away."""
        if len(self.direct_channels) == 0:
            return None
        inc_norm = normalize(incoming.text)
        if not inc_norm:
            return None
        for existing in reversed(self.segments[-30:]):
            if existing.channel == incoming.channel:
                continue
            if min(existing.t1, incoming.t1) - max(existing.t0, incoming.t0) <= 0:
                continue
            ratio = fuzz.ratio(inc_norm, normalize(existing.text))
            if ratio < CROSSTALK_SIMILARITY:
                continue
            inc_direct = incoming.channel in self.direct_channels
            exist_direct = existing.channel in self.direct_channels
            if inc_direct and not exist_direct:
                return existing
            if exist_direct and not inc_direct:
                return incoming
            return incoming  # both or neither direct: keep the one already in
        return None

    # --- queries -----------------------------------------------------------

    def last_id(self) -> str | None:
        return self.segments[-1].id if self.segments else None

    def get(self, segment_id: str) -> Segment | None:
        return self._by_id.get(segment_id)

    def new_since(self, segment_id: str | None) -> list[Segment]:
        """Segments that arrived after ``segment_id``.

        Arrival order, not timestamp order: a slow STT queue can deliver an older
        utterance after a newer one, and it is still new to the reasoner.
        """
        if segment_id is None:
            return list(self.segments)
        cut = self._arrival.get(segment_id)
        if cut is None:
            return list(self.segments)
        return [s for s in self.segments if self._arrival.get(s.id, 0) > cut]

    def words_since(self, segment_id: str | None) -> int:
        return sum(s.word_count() for s in self.new_since(segment_id))

    def total_words(self) -> int:
        return sum(s.word_count() for s in self.segments)

    def window(self, words: int, since_id: str | None = None) -> list[Segment]:
        """Whole segments from the end until the word budget is spent.

        Everything after ``since_id`` is always included, even past the budget:
        a burst matters more than older context (PLAN §5.3).
        """
        must_have = {s.id for s in self.new_since(since_id)} if since_id is not None else set()
        out: list[Segment] = []
        budget = words
        for seg in reversed(self.segments):
            if seg.id in must_have:
                out.append(seg)
                budget -= seg.word_count()
                continue
            if budget <= 0:
                break
            out.append(seg)
            budget -= seg.word_count()
        out.reverse()
        return out

    def duration(self) -> float:
        return self.segments[-1].t1 if self.segments else 0.0

    def speakers(self) -> list[str]:
        return sorted({s.speaker for s in self.segments if s.speaker})

    # --- persistence -------------------------------------------------------

    @classmethod
    def load_jsonl(cls, path: str | Path, direct_channels: Iterable[str] = ()) -> Transcript:
        t = cls(direct_channels)
        p = Path(path)
        if not p.is_file():
            return t
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                seg = Segment.model_validate(json.loads(line))
            except Exception:
                continue
            t.segments.append(seg)
            t._by_id[seg.id] = seg
            t._arrival[seg.id] = len(t._arrival)
        t.segments.sort(key=lambda s: (s.t0, t._arrival.get(s.id, 0)))
        t.adopt_counter()
        return t
