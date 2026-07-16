from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz


@dataclass(frozen=True, slots=True)
class _PdfWord:
    rectangle: fitz.Rect
    text: str
    block_index: int
    line_index: int


@dataclass(frozen=True, slots=True)
class _UrlPatch:
    uri: str
    prefix: tuple[_PdfWord, ...]
    suffix: tuple[_PdfWord, ...]
    trailing: tuple[_PdfWord, ...] = ()


def repair_fragmented_urls(source_pdf: Path, translated_pdf: Path) -> int:
    source = fitz.open(source_pdf)
    translated = fitz.open(translated_pdf)
    temporary = translated_pdf.with_name(f"{translated_pdf.stem}.url-repair.tmp.pdf")
    temporary.unlink(missing_ok=True)
    repair_count = 0
    try:
        for page_index in range(min(source.page_count, translated.page_count)):
            source_page = source[page_index]
            translated_page = translated[page_index]
            patches = _page_patches(source_page, translated_page)
            if not patches:
                continue
            _apply_patches(translated_page, patches)
            repair_count += len(patches)
        if repair_count:
            translated.save(
                temporary,
                garbage=4,
                deflate=True,
                deflate_fonts=True,
            )
    finally:
        translated.close()
        source.close()

    if repair_count:
        temporary.replace(translated_pdf)
    else:
        temporary.unlink(missing_ok=True)
    return repair_count


def _page_patches(
    source_page: fitz.Page,
    translated_page: fitz.Page,
) -> tuple[_UrlPatch, ...]:
    words = _page_words(translated_page)
    patches: list[_UrlPatch] = []
    used_words: set[int] = set()
    for uri in _horizontal_source_uris(source_page):
        matched, positions = _match_uri(words, uri, used_words)
        used_words.update(positions)
        patch = _fragmented_prefix(uri, matched)
        if patch is not None:
            patches.append(
                _UrlPatch(
                    uri=patch.uri,
                    prefix=patch.prefix,
                    suffix=patch.suffix,
                    trailing=_overlapping_trailing_words(words, patch),
                )
            )
    return tuple(patches)


def _horizontal_source_uris(page: fitz.Page) -> tuple[str, ...]:
    uris: list[str] = []
    for link in page.get_links():
        uri = str(link.get("uri", "")).strip()
        rectangle = fitz.Rect(link.get("from", fitz.Rect()))
        if uri and rectangle.width >= rectangle.height:
            uris.append(uri)
    return tuple(uris)


def _page_words(page: fitz.Page) -> tuple[_PdfWord, ...]:
    return tuple(
        _PdfWord(
            rectangle=fitz.Rect(raw_word[:4]),
            text=str(raw_word[4]),
            block_index=int(raw_word[5]),
            line_index=int(raw_word[6]),
        )
        for raw_word in page.get_text("words")
    )


def _match_uri(
    words: tuple[_PdfWord, ...],
    uri: str,
    used_words: set[int],
) -> tuple[tuple[_PdfWord, ...], tuple[int, ...]]:
    for start in range(len(words)):
        if start in used_words:
            continue
        combined = ""
        matched: list[_PdfWord] = []
        positions: list[int] = []
        for position, word in enumerate(words[start:], start=start):
            if position in used_words:
                break
            candidate = combined + word.text
            if uri.startswith(candidate):
                combined = candidate
                matched.append(word)
                positions.append(position)
            elif word.text.startswith(uri[len(combined) :]):
                matched.append(word)
                positions.append(position)
                combined = uri
            else:
                break
            if combined == uri:
                return tuple(matched), tuple(positions)
    return (), ()


def _fragmented_prefix(
    uri: str,
    matched: tuple[_PdfWord, ...],
) -> _UrlPatch | None:
    selected: _UrlPatch | None = None
    for split in range(1, len(matched)):
        prefix = matched[:split]
        suffix = matched[split:]
        prefix_lines = {(word.block_index, word.line_index) for word in prefix}
        suffix_lines = {(word.block_index, word.line_index) for word in suffix}
        prefix_text = "".join(word.text for word in prefix)
        suffix_text = "".join(word.text for word in suffix)
        if (
            len(prefix_lines) >= 3
            and len(suffix_lines) <= 2
            and sum(len(word.text) == 1 for word in prefix) >= 3
            and len(prefix_text) >= 4
            and len(suffix_text) >= 5
            and prefix_text + suffix_text == uri
        ):
            selected = _UrlPatch(uri=uri, prefix=prefix, suffix=suffix)
    return selected


def _overlapping_trailing_words(
    words: tuple[_PdfWord, ...],
    patch: _UrlPatch,
) -> tuple[_PdfWord, ...]:
    last_suffix = patch.suffix[-1]
    trailing: list[_PdfWord] = []
    match_finished = False
    for word in words:
        if word == last_suffix:
            match_finished = True
            continue
        if not match_finished:
            continue
        if word.block_index != last_suffix.block_index:
            break
        vertical_gap = word.rectangle.y0 - last_suffix.rectangle.y1
        if word.line_index > last_suffix.line_index and vertical_gap < word.rectangle.height:
            trailing.append(word)
        else:
            break
    return tuple(trailing)


def _apply_patches(page: fitz.Page, patches: tuple[_UrlPatch, ...]) -> None:
    for patch in patches:
        for word in patch.prefix + patch.suffix + patch.trailing:
            rectangle = word.rectangle + (0.2, 0.2, -0.2, -0.2)
            page.add_redact_annot(rectangle, fill=(1, 1, 1))
    page.apply_redactions(images=0, graphics=0, text=0)

    for patch in patches:
        prefix_text = "".join(word.text for word in patch.prefix)
        suffix_text = "".join(word.text for word in patch.suffix)
        font_size = min(
            9.0,
            max(
                6.0,
                sum(
                    word.rectangle.height for word in patch.prefix + patch.suffix
                )
                / len(patch.prefix + patch.suffix),
            ),
        )
        x_position = min(word.rectangle.x0 for word in patch.prefix + patch.suffix)
        available_width = max(
            word.rectangle.x1 for word in patch.prefix + patch.suffix
        ) - x_position
        longest_width = max(
            fitz.get_text_length(prefix_text, fontname="cour", fontsize=font_size),
            fitz.get_text_length(suffix_text, fontname="cour", fontsize=font_size),
        )
        if longest_width > available_width:
            font_size = max(6.0, font_size * available_width / longest_width)
        first_baseline = patch.prefix[0].rectangle.y0 + font_size
        line_step = font_size * 1.35
        page.insert_text(
            (x_position, first_baseline),
            prefix_text,
            fontname="cour",
            fontsize=font_size,
            color=(0.1, 0.25, 0.7),
        )
        page.insert_text(
            (x_position, first_baseline + line_step),
            suffix_text,
            fontname="cour",
            fontsize=font_size,
            color=(0.1, 0.25, 0.7),
        )
        if patch.trailing:
            first_line = patch.trailing[0].line_index
            for word in patch.trailing:
                trailing_font = (
                    "china-s"
                    if any("\u3400" <= char <= "\u9fff" for char in word.text)
                    else "helv"
                )
                page.insert_text(
                    (
                        word.rectangle.x0,
                        first_baseline
                        + line_step * (2 + word.line_index - first_line),
                    ),
                    word.text,
                    fontname=trailing_font,
                    fontsize=font_size,
                )
        suffix_width = fitz.get_text_length(
            suffix_text,
            fontname="cour",
            fontsize=font_size,
        )
        page.insert_link(
            {
                "kind": fitz.LINK_URI,
                "from": fitz.Rect(
                    x_position,
                    first_baseline - font_size,
                    x_position + max(longest_width, suffix_width),
                    first_baseline + line_step + font_size * 0.25,
                ),
                "uri": patch.uri,
            }
        )
