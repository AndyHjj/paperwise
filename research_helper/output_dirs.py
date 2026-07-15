from __future__ import annotations

import json
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

_ARXIV_ID = re.compile(
    r"(?<!\d)(?P<year>\d{4})[._](?P<number>\d{4,5})(?:v\d+)?(?!\d)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PaperDirectoryResolution:
    path: Path
    identity: str
    duplicate_dirs: tuple[Path, ...] = ()


def canonical_arxiv_id(value: str | None) -> str | None:
    """Return a version-independent modern Arxiv ID, if one is present."""
    if not value or value.strip().casefold() == "local":
        return None
    match = _ARXIV_ID.search(value)
    if match is None:
        return None
    return f"{match.group('year')}.{match.group('number')}"


def detect_arxiv_id(*values: str | None) -> str | None:
    for value in values:
        detected = canonical_arxiv_id(value)
        if detected is not None:
            return detected
    return None


def resolve_paper_dir(
    outputs_dir: Path,
    *,
    arxiv_id: str | None,
    title: str,
) -> PaperDirectoryResolution:
    """Resolve a paper to one stable output directory without moving old data."""
    outputs_dir = Path(outputs_dir)
    canonical_id = canonical_arxiv_id(arxiv_id)
    title_key = _title_key(title)
    candidates = _matching_directories(outputs_dir, canonical_id, title_key)

    canonical_path = (
        outputs_dir / canonical_id.replace(".", "_")
        if canonical_id is not None
        else None
    )
    if canonical_path is not None and canonical_path in candidates:
        selected = canonical_path
    elif candidates:
        selected = min(
            candidates,
            key=lambda path: (
                not _metadata_has_arxiv_id(path, canonical_id),
                path.name.casefold(),
            ),
        )
    elif canonical_path is not None:
        selected = canonical_path
    else:
        selected = outputs_dir / _safe_title(title)

    identity = (
        f"arxiv:{canonical_id}"
        if canonical_id is not None
        else f"title:{title_key or _title_key(selected.name)}"
    )
    duplicates = tuple(
        path
        for path in sorted(candidates, key=lambda item: item.name.casefold())
        if path != selected
    )
    return PaperDirectoryResolution(selected, identity, duplicates)


def write_paper_metadata(directory: Path, incoming: dict) -> Path:
    """Atomically update meta.json without replacing rich fields with blanks."""
    directory.mkdir(parents=True, exist_ok=True)
    existing = _read_metadata(directory)
    merged = dict(existing)
    sparse_local_metadata = (
        not str(incoming.get("abstract", "")).strip()
        and not str(incoming.get("published", "")).strip()
        and _authors_are_unknown(incoming.get("authors"))
    )
    for key, value in incoming.items():
        if key == "arxiv_id":
            if canonical_arxiv_id(str(value)) is not None or not merged.get(key):
                merged[key] = value
        elif key == "title" and sparse_local_metadata and merged.get("title"):
            continue
        elif _has_meaningful_value(key, value):
            merged[key] = value
        elif key not in merged:
            merged[key] = value

    path = directory / "meta.json"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=directory,
        delete=False,
    ) as temporary:
        json.dump(merged, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)
    return path


def _matching_directories(
    outputs_dir: Path,
    canonical_id: str | None,
    title_key: str,
) -> tuple[Path, ...]:
    if not outputs_dir.is_dir():
        return ()
    matches: list[Path] = []
    for directory in outputs_dir.iterdir():
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        metadata = _read_metadata(directory)
        metadata_id = canonical_arxiv_id(str(metadata.get("arxiv_id", "")))
        directory_id = canonical_arxiv_id(directory.name)
        metadata_title = _title_key(str(metadata.get("title", "")))
        id_matches = canonical_id is not None and canonical_id in {
            metadata_id,
            directory_id,
        }
        title_matches = bool(title_key and metadata_title == title_key)
        if id_matches or title_matches:
            matches.append(directory)
    return tuple(matches)


def _metadata_has_arxiv_id(directory: Path, canonical_id: str | None) -> bool:
    if canonical_id is None:
        return False
    metadata = _read_metadata(directory)
    return canonical_arxiv_id(str(metadata.get("arxiv_id", ""))) == canonical_id


def _read_metadata(directory: Path) -> dict:
    path = directory / "meta.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _has_meaningful_value(key: str, value: object) -> bool:
    if key == "authors":
        return not _authors_are_unknown(value)
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return value is not None


def _authors_are_unknown(value: object) -> bool:
    if not isinstance(value, (list, tuple)) or not value:
        return True
    authors = {str(author).strip().casefold() for author in value}
    return not authors or authors <= {"unknown"}


def _title_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    key = "".join(char for char in normalized if char.isalnum())
    return key if len(key) >= 12 else ""


def _safe_title(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title).strip()
    safe = re.sub(r"[^\w-]+", "_", normalized, flags=re.UNICODE)
    safe = re.sub(r"_+", "_", safe).strip("._")
    return safe[:80] or "paper"
