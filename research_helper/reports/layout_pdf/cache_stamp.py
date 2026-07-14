from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

_CACHE_VERSION = "paperwise-layout-v2"


@dataclass(frozen=True, slots=True)
class TranslationCacheStamp:
    source_digest: str
    translated_digest: str
    selected_pages: int

    def serialize(self) -> str:
        return (
            f"{_CACHE_VERSION}\n"
            f"source_sha256={self.source_digest}\n"
            f"translated_sha256={self.translated_digest}\n"
            f"selected_pages={self.selected_pages}\n"
        )


def record_translation_cache(
    source_pdf: Path,
    translated_pdf: Path,
    selected_pages: int,
) -> Path:
    stamp = TranslationCacheStamp(
        source_digest=_sha256(source_pdf),
        translated_digest=_sha256(translated_pdf),
        selected_pages=selected_pages,
    )
    path = translation_cache_path(translated_pdf)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as temporary:
        temporary.write(stamp.serialize())
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)
    return path


def translation_cache_matches(
    source_pdf: Path,
    translated_pdf: Path,
    selected_pages: int,
) -> bool:
    path = translation_cache_path(translated_pdf)
    if not translated_pdf.is_file() or not path.is_file():
        return False
    stamp = _parse_stamp(path)
    if stamp is None or stamp.selected_pages != selected_pages:
        return False
    return (
        stamp.source_digest == _sha256(source_pdf)
        and stamp.translated_digest == _sha256(translated_pdf)
    )


def translation_cache_path(translated_pdf: Path) -> Path:
    return translated_pdf.with_suffix(f"{translated_pdf.suffix}.cache-v2")


def _parse_stamp(path: Path) -> TranslationCacheStamp | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) != 4 or lines[0] != _CACHE_VERSION:
            return None
        source_digest = lines[1].removeprefix("source_sha256=")
        translated_digest = lines[2].removeprefix("translated_sha256=")
        selected_pages = int(lines[3].removeprefix("selected_pages="))
    except (OSError, UnicodeError, ValueError):
        return None
    if len(source_digest) != 64 or len(translated_digest) != 64:
        return None
    return TranslationCacheStamp(source_digest, translated_digest, selected_pages)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
