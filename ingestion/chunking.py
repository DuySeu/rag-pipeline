from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from itertools import pairwise

# Bump whenever a strategy's logic changes, so stores built with the old logic are told apart
CHUNKING_VERSION = 1
# Coarsest first: paragraph, line, sentence, clause, word
SEPARATORS = ("\n\n", "\n", ". ", "? ", "! ", "; ", " ")
# A Markdown heading, or a Vietnamese legal heading such as "Chương II", "Mục 1." or "Điều 5."
HEADING = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]+\S"
    r"|(?:Phần|Chương|Mục|Điều)[ \t]+(?:thứ[ \t]+\w+|\d+[a-zđ]?|[IVXLCDM]+)[ \t]*(?:[.:]|$))",
    re.MULTILINE | re.IGNORECASE,
)

Splitter = Callable[[str, int, int], list[str]]


# Split text into windows of at most size characters that overlap by overlap characters
def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        piece = text[start : start + size].strip()
        if piece:
            chunks.append(piece)
        if start + size >= len(text):
            break
        start += size - overlap
    return chunks


# Pack whole lines into chunks of at most size characters, windowing only a line longer than that
def chunk_lines(text: str, size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        if len(line) > size:
            chunks.extend(filter(None, [current]))
            chunks.extend(chunk_text(line, size, overlap))
            current = ""
        elif current and len(current) + 1 + len(line) > size:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    return chunks + [current] if current.strip() else chunks


# Cut text at the coarsest separator that fits, then pack the pieces up to size, carrying
# trailing pieces of at most overlap characters into the next chunk
def chunk_recursive(text: str, size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    window: list[str] = []
    length = 0
    for piece in split_pieces(text, size, SEPARATORS):
        if window and length + len(piece) > size:
            chunks.append("".join(window).strip())
            # Drop leading pieces until the rest fits the overlap and leaves room for this piece
            while window and (length > overlap or length + len(piece) > size):
                length -= len(window.pop(0))
        window.append(piece)
        length += len(piece)
    chunks.append("".join(window).strip())
    return [c for c in chunks if c]


# Start a new chunk at every heading; a section longer than size is split recursively
def chunk_headings(text: str, size: int, overlap: int) -> list[str]:
    # ponytail: a heading-only section (e.g. "Chương I") becomes a tiny chunk and sub-chunks of
    # a long section lose their heading; merge or prefix them if eval shows retrieval suffers
    starts = sorted({0, *(m.start() for m in HEADING.finditer(text)), len(text)})
    chunks: list[str] = []
    for start, end in pairwise(starts):
        section = text[start:end].strip()
        if len(section) > size:
            chunks.extend(chunk_recursive(section, size, overlap))
        elif section:
            chunks.append(section)
    return chunks


# Split text into pieces of at most size characters that join back into the text
def split_pieces(text: str, size: int, separators: Sequence[str]) -> list[str]:
    if len(text) <= size:
        return [text]
    separator = next((s for s in separators if s in text), None)
    if separator is None:
        return [text[i : i + size] for i in range(0, len(text), size)]

    # Keep each separator on the end of its piece so nothing is lost
    parts = [p + separator for p in text.split(separator)]
    parts[-1] = parts[-1].removesuffix(separator)
    rest = separators[separators.index(separator) + 1 :]
    return [piece for part in parts if part for piece in split_pieces(part, size, rest)]


# Strategy names as written in config.yaml
STRATEGIES: dict[str, Splitter] = {
    "fixed": chunk_text,
    "recursive": chunk_recursive,
    "heading": chunk_headings,
}
