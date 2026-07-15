from __future__ import annotations

import json
from pathlib import Path

import fitz

from research_helper.cli import _meta_from_pdf
from research_helper.output_dirs import (
    canonical_arxiv_id,
    detect_arxiv_id,
    resolve_paper_dir,
    write_paper_metadata,
)


def _write_meta(directory: Path, *, arxiv_id: str, title: str) -> Path:
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text(
        json.dumps({"arxiv_id": arxiv_id, "title": title}),
        encoding="utf-8",
    )
    return directory


def test_arxiv_identity_ignores_version_and_filename_separator() -> None:
    assert canonical_arxiv_id("https://arxiv.org/abs/2502.14802v3") == "2502.14802"
    assert detect_arxiv_id("downloaded_2502_14802.pdf") == "2502.14802"


def test_new_arxiv_paper_uses_stable_id_directory(tmp_path: Path) -> None:
    first = resolve_paper_dir(
        tmp_path,
        arxiv_id="2502.14802v1",
        title="Original title",
    )
    second = resolve_paper_dir(
        tmp_path,
        arxiv_id="2502.14802v4",
        title="A revised title",
    )

    assert first.path == tmp_path / "2502_14802"
    assert second.path == first.path
    assert first.identity == "arxiv:2502.14802"


def test_existing_canonical_directory_wins_and_duplicates_are_reported(
    tmp_path: Path,
) -> None:
    title = "From RAG to Memory: Non-Parametric Continual Learning"
    canonical = _write_meta(tmp_path / "2502_14802", arxiv_id="local", title=title)
    old_title_dir = _write_meta(
        tmp_path / "From_RAG_to_Memory",
        arxiv_id="2502.14802v2",
        title=title,
    )

    resolution = resolve_paper_dir(
        tmp_path,
        arxiv_id="2502.14802",
        title=title,
    )

    assert resolution.path == canonical
    assert resolution.duplicate_dirs == (old_title_dir,)


def test_local_paper_reuses_existing_exact_title_directory(tmp_path: Path) -> None:
    title = "A sufficiently distinctive paper title"
    existing = _write_meta(tmp_path / "legacy-name", arxiv_id="local", title=title)

    resolution = resolve_paper_dir(tmp_path, arxiv_id="local", title=title)

    assert resolution.path == existing
    assert resolution.duplicate_dirs == ()


def test_local_pdf_detects_arxiv_id_from_page_text(tmp_path: Path) -> None:
    path = tmp_path / "conference-version.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((20, 30), "Preprint available at arXiv:2502.14802v2")
    document.save(path)
    document.close()

    meta = _meta_from_pdf(path)

    assert meta.arxiv_id == "2502.14802"


def test_sparse_local_metadata_does_not_replace_rich_existing_metadata(
    tmp_path: Path,
) -> None:
    directory = _write_meta(
        tmp_path / "2502_14802",
        arxiv_id="2502.14802",
        title="Canonical paper title",
    )
    write_paper_metadata(
        directory,
        {
            "authors": ["Ada Lovelace"],
            "published": "2025-02-20",
            "abstract": "A complete abstract.",
            "categories": ["cs.CL"],
        },
    )
    write_paper_metadata(
        directory,
        {
            "arxiv_id": "2502.14802",
            "title": "downloaded-file",
            "authors": ["Unknown"],
            "published": "",
            "abstract": "",
            "categories": [],
        },
    )

    metadata = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    assert metadata["title"] == "Canonical paper title"
    assert metadata["arxiv_id"] == "2502.14802"
    assert metadata["authors"] == ["Ada Lovelace"]
    assert metadata["published"] == "2025-02-20"
    assert metadata["abstract"] == "A complete abstract."
    assert metadata["categories"] == ["cs.CL"]
