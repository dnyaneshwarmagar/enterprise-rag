import re
from dataclasses import dataclass

from app.observe import info, span

_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")


@dataclass
class TextChunk:
    """One indexed piece of a document, with the pages it came from."""

    text: str
    page_start: int | None = None
    page_end: int | None = None


def chunk_text(text: str, chunk_size: int = 1500, overlap: int = 200) -> list[str]:
    """Split a single document body. Every returned string is at most chunk_size."""
    return [chunk.text for chunk in chunk_pages([(None, text)], chunk_size=chunk_size, overlap=overlap)]


def chunk_pages(
    pages: list[tuple[int | None, str]],
    chunk_size: int = 1500,
    overlap: int = 200,
) -> list[TextChunk]:
    """Pack page text into chunks that never exceed chunk_size, with overlap."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    overlap = max(0, min(overlap, chunk_size // 2))

    with span("Chunking", pages=len(pages), chunk_size=chunk_size, overlap=overlap) as record:
        segments: list[tuple[str, int | None]] = []
        for page, text in pages:
            segments.extend(_segments(text or "", page, chunk_size))

        chunks: list[TextChunk] = []
        current = ""
        page_start: int | None = None
        page_end: int | None = None

        for text, page in segments:
            candidate = text if not current else f"{current}\n\n{text}"
            if len(candidate) <= chunk_size:
                if not current:
                    page_start = page
                current = candidate
                page_end = page
                continue

            if current:
                chunks.append(TextChunk(current, page_start, page_end))
                tail = current[-overlap:].lstrip() if overlap else ""
                current = f"{tail}\n\n{text}".strip() if tail else text
                if len(current) > chunk_size:
                    current = text
                    page_start = page
                else:
                    page_start = page_end if tail else page
                page_end = page
            else:
                current = text
                page_start = page
                page_end = page

        if current.strip():
            chunks.append(TextChunk(current.strip(), page_start, page_end))

        info(f"Generated {len(chunks)} chunks")
        record.outputs(status="ok" if chunks else "failed", chunks=len(chunks))
        return chunks


def _segments(text: str, page: int | None, chunk_size: int) -> list[tuple[str, int | None]]:
    """Break text into pieces that are each at most chunk_size."""
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    pieces: list[str] = []
    for block in blocks:
        if len(block) <= chunk_size:
            pieces.append(block)
            continue
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if len(lines) > 1:
            buffer = ""
            for line in lines:
                if len(line) > chunk_size:
                    if buffer:
                        pieces.append(buffer)
                        buffer = ""
                    pieces.extend(_hard_split(line, chunk_size))
                    continue
                joined = line if not buffer else f"{buffer}\n{line}"
                if len(joined) <= chunk_size:
                    buffer = joined
                else:
                    if buffer:
                        pieces.append(buffer)
                    buffer = line
            if buffer:
                pieces.append(buffer)
        else:
            pieces.extend(_hard_split(block, chunk_size))
    return [(piece, page) for piece in pieces if piece.strip()]


def _hard_split(text: str, chunk_size: int) -> list[str]:
    """Split a single block on sentence boundaries, then on a hard window."""
    parts: list[str] = []
    buffer = ""
    for sentence in _SENTENCE_BREAK.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > chunk_size:
            if buffer:
                parts.append(buffer)
                buffer = ""
            parts.extend(sentence[i : i + chunk_size] for i in range(0, len(sentence), chunk_size))
            continue
        joined = sentence if not buffer else f"{buffer} {sentence}"
        if len(joined) <= chunk_size:
            buffer = joined
        else:
            if buffer:
                parts.append(buffer)
            buffer = sentence
    if buffer:
        parts.append(buffer)
    return parts
