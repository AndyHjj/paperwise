"""Generate a side-by-side translated PDF for Zotero attachments."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

import fitz

from research_helper.llm import client as llm
from research_helper.readers.arxiv_reader import PaperMeta
from research_helper.utils.cache import load_cache, save_cache

PAGE_GAP = 12
MIN_FONT_SIZE = 5.5
MAX_BLOCK_CHARS = 3500

SYSTEM_PROMPT = """\
You are a rigorous academic paper translation engine.
Translate English academic paper text into Simplified Chinese.
Only output the translation. Do not explain, summarize, or add translator notes.
Preserve formulas, numbers, citation IDs, figure/table references, proper nouns, and code.
"""

TRANSLATE_PROMPT = """\
Translate the following paper text block into Simplified Chinese.
Keep the meaning faithful and do not expand the content.

---
{text}
---
"""


@dataclass
class TextBlock:
    page_index: int
    block_index: int
    rect: tuple[float, float, float, float]
    text: str

    @property
    def id(self) -> str:
        return f"p{self.page_index}_b{self.block_index}"


def generate(
    source_pdf: Path,
    paper_dir: Path,
    meta: PaperMeta,
    force: bool = False,
    max_pages: int | None = None,
) -> Path:
    """Create a dual-page translated PDF and return its path."""
    paper_dir.mkdir(parents=True, exist_ok=True)
    output_path = paper_dir / f"{_safe_stem(source_pdf.stem)}_paperwise_bilingual.pdf"
    if output_path.exists() and not force:
        return output_path

    blocks = _extract_blocks(source_pdf, max_pages=max_pages)
    cache = load_cache(paper_dir, "bilingual_pdf")
    if cache and not force and cache.get("block_ids") == [b.id for b in blocks]:
        translations = cache.get("translations", {})
    else:
        translations = _translate_blocks(blocks)
        save_cache(
            paper_dir,
            "bilingual_pdf",
            {
                "title": meta.title,
                "source_pdf": str(source_pdf),
                "block_ids": [b.id for b in blocks],
                "translations": translations,
            },
        )

    _render_dual_pdf(source_pdf, output_path, blocks, translations, max_pages=max_pages)
    return output_path


def _extract_blocks(source_pdf: Path, max_pages: int | None = None) -> list[TextBlock]:
    doc = fitz.open(source_pdf)
    try:
        page_count = min(doc.page_count, max_pages) if max_pages else doc.page_count
        blocks: list[TextBlock] = []
        global_overflow: list[tuple[int, str]] = []
        for page_index in range(page_count):
            page = doc[page_index]
            raw_blocks = page.get_text("blocks", sort=True)
            for block_index, raw in enumerate(raw_blocks):
                if len(raw) < 5:
                    continue
                x0, y0, x1, y1, text = raw[:5]
                text = _clean_text(str(text))
                if not _is_translatable(text):
                    continue
                blocks.append(
                    TextBlock(
                        page_index=page_index,
                        block_index=block_index,
                        rect=(float(x0), float(y0), float(x1), float(y1)),
                        text=text,
                    )
                )
        return blocks
    finally:
        doc.close()


def _translate_blocks(blocks: list[TextBlock]) -> dict[str, str]:
    translations: dict[str, str] = {}
    for block in blocks:
        chunks = _split_text(block.text, MAX_BLOCK_CHARS)
        translated_chunks = [
            llm.complete(
                SYSTEM_PROMPT,
                TRANSLATE_PROMPT.format(text=chunk),
                max_tokens=5000,
            ).strip()
            for chunk in chunks
        ]
        translations[block.id] = "\n".join(part for part in translated_chunks if part)
    return translations


def _render_dual_pdf(
    source_pdf: Path,
    output_path: Path,
    blocks: list[TextBlock],
    translations: dict[str, str],
    max_pages: int | None = None,
) -> None:
    source = fitz.open(source_pdf)
    output = fitz.open()
    blocks_by_page: dict[int, list[TextBlock]] = {}
    for block in blocks:
        blocks_by_page.setdefault(block.page_index, []).append(block)

    try:
        page_count = min(source.page_count, max_pages) if max_pages else source.page_count
        global_overflow: list[tuple[int, str]] = []
        for page_index in range(page_count):
            original = source[page_index]
            rect = original.rect
            target = output.new_page(width=rect.width * 2 + PAGE_GAP, height=rect.height)
            left = fitz.Rect(0, 0, rect.width, rect.height)
            right = fitz.Rect(rect.width + PAGE_GAP, 0, rect.width * 2 + PAGE_GAP, rect.height)

            target.show_pdf_page(left, source, page_index)
            target.show_pdf_page(right, source, page_index)
            overflow: list[str] = []
            for block in blocks_by_page.get(page_index, []):
                translated = translations.get(block.id, "").strip()
                if not translated:
                    continue
                x0, y0, x1, y1 = block.rect
                block_rect = fitz.Rect(
                    right.x0 + x0,
                    right.y0 + y0,
                    right.x0 + x1,
                    right.y0 + y1,
                ) & right
                if block_rect.width < 8 or block_rect.height < 8:
                    overflow.append(translated)
                    continue
                target.draw_rect(block_rect, color=None, fill=(1, 1, 1), overlay=True)
                if not _insert_text(target, block_rect, translated):
                    overflow.append(translated)
                    target.insert_htmlbox(
                        block_rect,
                        "<div style='font-family:sans-serif;font-size:6pt;color:#666'>"
                        "See translation appendix</div>",
                        overlay=True,
                    )
            if overflow:
                global_overflow.extend((page_index, text) for text in overflow)

        if global_overflow:
            _draw_overflow_pages(output, source[0].rect, global_overflow)

        output.set_metadata(
            {
                **source.metadata,
                "producer": "Paperwise",
                "title": f"{source.metadata.get('title') or source_pdf.stem} - Paperwise bilingual",
            }
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output.save(output_path, garbage=4, deflate=True)
    finally:
        output.close()
        source.close()


def _insert_text(page: fitz.Page, rect: fitz.Rect, text: str) -> bool:
    escaped = html.escape(text).replace("\n", "<br>")
    font_size = min(10.5, max(MIN_FONT_SIZE, rect.height * 0.45))
    while font_size >= MIN_FONT_SIZE:
        spare, _ = page.insert_htmlbox(
            rect,
            f"<div style='font-family:sans-serif;font-size:{font_size}pt;"
            f"line-height:1.15'>{escaped}</div>",
            scale_low=1,
            overlay=True,
        )
        if spare >= 0:
            return True
        font_size -= 0.5
    spare, _ = page.insert_htmlbox(
        rect,
        f"<div style='font-family:sans-serif;font-size:{MIN_FONT_SIZE}pt;"
        f"line-height:1.05'>{escaped}</div>",
        scale_low=0.65,
        overlay=True,
    )
    return spare >= 0


def _draw_overflow_pages(
    output: fitz.Document,
    source_rect: fitz.Rect,
    overflow: list[tuple[int, str]],
) -> None:
    chunks: list[str] = []
    for page_index, text in overflow:
        text = text.strip()
        if text:
            chunks.append(f"[source page {page_index + 1}]\n{text}")
    if not chunks:
        return

    box = fitz.Rect(40, 54, source_rect.width * 2 + PAGE_GAP - 40, source_rect.height - 40)
    capacity = max(int(box.width * box.height / 68), 1200)
    packed = _split_text("\n\n".join(chunks), capacity)
    total = len(packed)
    for index, text in enumerate(packed, 1):
        page = output.new_page(width=source_rect.width * 2 + PAGE_GAP, height=source_rect.height)
        page.insert_text((40, 34), f"Paperwise translation appendix {index}/{total}", fontsize=10)
        escaped = html.escape(text).replace("\n", "<br>")
        page.insert_htmlbox(
            box,
            f"<div style='font-family:sans-serif;font-size:9pt;line-height:1.32'>{escaped}</div>",
            scale_low=0.65,
            overlay=True,
        )


def _clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_translatable(text: str) -> bool:
    if len(text) < 20:
        return False
    letters = sum(1 for ch in text if ch.isalpha())
    return letters >= max(8, len(text) * 0.25)


def _split_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    current = text.strip()
    while current:
        if len(current) <= max_chars:
            parts.append(current)
            break
        split_at = max(
            current.rfind("\n", 0, max_chars),
            current.rfind(". ", 0, max_chars) + 1,
            current.rfind("; ", 0, max_chars) + 1,
        )
        if split_at < max_chars // 2:
            split_at = max_chars
        parts.append(current[:split_at].strip())
        current = current[split_at:].strip()
    return parts


def _safe_stem(stem: str) -> str:
    return re.sub(r"[^\w.-]+", "_", stem).strip("._")[:80] or "paper"
