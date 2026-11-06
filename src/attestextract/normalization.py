"""Conservative search views with offsets into unchanged Unicode source text.

These views are retrieval aids, never scientific equivalence or OCR repair.
LaTeX math regions retain every character, including whitespace.
"""

from dataclasses import dataclass
import re


_PUNCTUATION = str.maketrans({
    "，": ",", "。": ".", "：": ":", "；": ";", "！": "!", "？": "?",
    "（": "(", "）": ")", "【": "[", "】": "]",
    "“": '"', "”": '"', "‘": "'", "’": "'",
})
_LATEX = re.compile(
    r"(?<!\\)\$\$[\s\S]*?(?<!\\)\$\$"
    r"|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)"
    r"|\\begin\{(?P<environment>[A-Za-z*]+)\}[\s\S]*?\\end\{(?P=environment)\}"
    r"|(?<![\\$])\$(?!\$)(?:\\[^\n]|[^$\\])*?(?<!\\)\$"
    # Without math delimiters the expression boundary is uncertain. Preserve
    # the rest of its line rather than guessing where a bare command ends.
    r"|\\[A-Za-z]+[^\r\n]*"
)


@dataclass(frozen=True)
class SearchView:
    text: str
    origins: tuple[tuple[int, int], ...]
    rules: tuple[str, ...]


def latex_spans(text: str) -> list[tuple[int, int]]:
    return [match.span() for match in _LATEX.finditer(text)]


def normalized_view(text: str, *, offset: int = 0) -> SearchView:
    """Collapse whitespace and map an explicit punctuation list outside math."""
    protected = bytearray(len(text))
    for start, end in latex_spans(text):
        protected[start:end] = b"\x01" * (end - start)
    output = []
    origins = []
    rules = set()
    index = 0
    while index < len(text):
        end = index + 1
        character = text[index]
        if not protected[index] and character.isspace():
            while end < len(text) and not protected[end] and text[end].isspace():
                end += 1
            character = " "
            if text[index:end] != character:
                rules.add("whitespace")
        elif not protected[index]:
            character = character.translate(_PUNCTUATION)
            if character != text[index]:
                rules.add("punctuation")
        output.append(character)
        origins.append((offset + index, offset + end))
        index = end
    return SearchView("".join(output), tuple(origins), tuple(sorted(rules)))
