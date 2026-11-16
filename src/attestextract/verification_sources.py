"""Bounded local reading tools over unchanged, source-indexed document text.

Search results are retrieval leads, not evidence of identity or attribution.
Every returned text is an exact Unicode slice; callers must retain the shown
ranges when deciding which quotations the model was actually allowed to cite.
"""

from __future__ import annotations

from array import array
from bisect import bisect_left, bisect_right
from copy import deepcopy
import re

from .chunking import _protected_positions, _sentence_ends
from .structure import build_structure


PASSAGE_CHARACTERS = 1600
SNIPPET_CHARACTERS = 400
MAX_READ_CHARACTERS = 24000


def _integer(value, low: int, high: int, name: str) -> None:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer between {low} and {high}.")


class SourceIndex:
    """Deterministic passages and local search, with no network or model use."""

    def __init__(self, text: str):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("SourceIndex requires nonempty original text.")
        self.text = text
        self.passages = self._passages()
        self._by_id = {item["passage_id"]: index for index, item in enumerate(self.passages)}
        self._starts = [item["start"] for item in self.passages]
        self._add_structure()
        # Case folding may expand characters (for example ß -> ss). Only
        # matches whose boundaries map to whole original characters are valid.
        pieces, offsets = [], array("Q", [0])
        for char in text:
            piece = char.casefold()
            pieces.append(piece)
            offsets.append(offsets[-1] + len(piece))
        self._folded, self._fold_offsets = "".join(pieces), offsets

    def _passages(self) -> list[dict]:
        text = self.text
        covered, blocked = _protected_positions(text)
        ends = [match.end() for match in re.finditer(r"\r?\n[ \t]*\r?\n", text)]
        if not ends or ends[-1] != len(text):
            ends.append(len(text))
        passages, paragraph_start = [], 0
        for paragraph_end in ends:
            sentence_ends = _sentence_ends(text, paragraph_start, paragraph_end, covered, blocked)
            start = paragraph_start
            while start < paragraph_end:
                cap = min(start + PASSAGE_CHARACTERS, paragraph_end)
                # One paragraph can contain thousands of sentences. Search its
                # sorted boundaries instead of rescanning all of them per slice.
                boundary_index = bisect_right(sentence_ends, cap) - 1
                boundary = sentence_ends[boundary_index] if boundary_index >= 0 else start
                if cap == paragraph_end:
                    end = cap
                elif boundary > start:
                    end = boundary
                else:
                    line = text.rfind("\n", start, cap)
                    end = line + 1 if line >= start else cap
                passages.append({
                    "passage_id": f"passage-{len(passages) + 1:06d}",
                    "start": start, "end": end, "text": text[start:end],
                    "partial_paragraph": start != paragraph_start or end != paragraph_end,
                    "forced_split": end != paragraph_end and end != boundary and text[end - 1] != "\n",
                })
                start = end
            paragraph_start = paragraph_end
        return passages

    def _ids_for_span(self, start: int, end: int) -> list[str]:
        first = max(0, bisect_right(self._starts, start) - 1)
        stop = bisect_left(self._starts, end)
        return [item["passage_id"] for item in self.passages[first:stop]]

    def _add_structure(self) -> None:
        structure = build_structure(self.text)
        heading_starts = [item.span.start for item in structure.headings]
        table_starts = [item.span.start for item in structure.tables]
        for passage in self.passages:
            hints = []
            previous_heading = bisect_right(heading_starts, passage["start"]) - 1
            if previous_heading >= 0:
                span = structure.headings[previous_heading].span
                hints.extend(self._ids_for_span(span.start, span.end))
            first = max(0, bisect_right(table_starts, passage["start"]) - 1)
            stop = bisect_left(table_starts, passage["end"])
            tables = [table for table in structure.tables[first:stop]
                      if table.span.start < passage["end"] and passage["start"] < table.span.end]
            for table in tables:
                spans = [table.rows[row] for row in table.header_rows]
                spans += table.captions + table.footnotes
                for span in spans:
                    hints.extend(self._ids_for_span(span.start, span.end))
            hints = list(dict.fromkeys(key for key in hints if key != passage["passage_id"]))
            passage.update(kind="table" if tables else "text",
                           context_passage_ids=hints[:12], context_hints_omitted=max(0, len(hints) - 12))

    def get(self, passage_id: str) -> dict:
        if not isinstance(passage_id, str) or passage_id not in self._by_id:
            raise ValueError("Unknown source passage ID.")
        return deepcopy(self.passages[self._by_id[passage_id]])

    def search(self, query: str, offset: int = 0, limit: int = 6) -> dict:
        """Find literal Unicode-casefold matches; no semantic claims or regex."""
        if not isinstance(query, str) or not query.strip() or len(query) > 256:
            raise ValueError("Search query must contain 1–256 nonblank source-search characters.")
        _integer(offset, 0, 1000000000, "offset")
        _integer(limit, 1, 12, "limit")
        needle, position, count, matches = query.casefold(), 0, 0, []
        while (position := self._folded.find(needle, position)) >= 0:
            folded_end = position + len(needle)
            start, end = bisect_left(self._fold_offsets, position), bisect_left(self._fold_offsets, folded_end)
            aligned = (self._fold_offsets[start] == position
                       and end < len(self._fold_offsets) and self._fold_offsets[end] == folded_end)
            position += 1
            if not aligned:
                continue
            if offset <= count < offset + limit:
                identifiers = self._ids_for_span(start, end)
                passage = self.get(identifiers[0])
                left = max(passage["start"], start - SNIPPET_CHARACTERS // 2)
                right = min(passage["end"], max(end, left + SNIPPET_CHARACTERS))
                left = max(passage["start"], right - SNIPPET_CHARACTERS)
                matches.append({**passage, "start": left, "end": right, "text": self.text[left:right],
                                "match_start": start, "match_end": end,
                                "continuation_passage_ids": identifiers[1:], "snippet": True})
            count += 1
        has_more = offset + len(matches) < count
        return {"query": query, "matches": matches, "total": count, "offset": offset,
                "has_more": has_more, "next_offset": offset + len(matches) if has_more else None,
                "search_mode": "literal_casefold", "notice": "Search matches are leads, not semantic verification."}

    def _read_indices(self, indices: list[int], max_chars: int) -> dict:
        _integer(max_chars, 1, MAX_READ_CHARACTERS, "max_chars")
        passages, omitted, used = [], [], 0
        for index in dict.fromkeys(indices):
            item = self.passages[index]
            size = item["end"] - item["start"]
            if used + size > max_chars:
                omitted.append(item["passage_id"])
            else:
                passages.append(deepcopy(item))
                used += size
        return {"passages": passages, "omitted_passage_ids": omitted, "has_more": bool(omitted),
                "returned_characters": used,
                "notice": "Only returned passages were read; omitted passages remain unexamined."}

    def read(self, passage_ids: list[str], neighbors: int = 0, max_chars: int = 6000) -> dict:
        if not isinstance(passage_ids, list) or not 1 <= len(passage_ids) <= 3:
            raise ValueError("Read requires one to three source passage IDs.")
        _integer(neighbors, 0, 1, "neighbors")
        for key in passage_ids:
            self.get(key)  # Validate every requested ID before returning anything.
        requested = list(dict.fromkeys(self._by_id[key] for key in passage_ids))
        adjacent = [other for index in requested for other in range(index - neighbors, index + neighbors + 1)
                    if 0 <= other < len(self.passages) and other not in requested]
        # Requested passages take precedence over incidental neighbors.
        return self._read_indices([*requested, *adjacent], max_chars)

    def nearby(self, spans: list[dict], max_chars: int = 6000) -> dict:
        if not isinstance(spans, list):
            raise ValueError("Nearby requires source span dictionaries.")
        indices = []
        for span in spans:
            if not isinstance(span, dict):
                raise ValueError("Nearby requires source span dictionaries.")
            start, end = span.get("start"), span.get("end")
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(self.text):
                raise ValueError("Nearby span is outside the original Unicode source.")
            indices.extend(self._by_id[key] for key in self._ids_for_span(start, end))
        if not spans:
            indices = [0]  # Seed a document with no localized extraction evidence.
        return self._read_indices(indices, max_chars)
