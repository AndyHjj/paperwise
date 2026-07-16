from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import fitz
import pytest
from click.testing import CliRunner

from research_helper import config
from research_helper.cli import main
from research_helper.readers.arxiv_reader import PaperMeta
from research_helper.reports import layout_pdf
from research_helper.reports.layout_pdf.quality_warnings import QualityWarningSink


def _write_pdf(path: Path) -> Path:
    document = fitz.open()
    document.new_page(width=220, height=300).insert_text((20, 30), "source token 42")
    document.save(path)
    document.close()
    return path


def _fake_generate(output: Path) -> Callable[..., Path]:
    def generate(
        source_pdf: Path,
        paper_dir: Path,
        meta: PaperMeta,
        force: bool = False,
        max_pages: int | None = None,
        warning_sink: QualityWarningSink | None = None,
    ) -> Path:
        del source_pdf, paper_dir, meta, force, max_pages
        _write_pdf(output)
        assert warning_sink is not None
        warning_sink(("PDF fidelity check failed:\n- page 1 font is too small",))
        return output

    return generate


def test_cli_prints_quality_warnings_for_a_published_pdf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_pdf(tmp_path / "source.pdf")
    output = tmp_path / "published.pdf"
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "placeholder")
    monkeypatch.setattr(layout_pdf, "generate", _fake_generate(output))

    result = CliRunner().invoke(
        main,
        ["translate", "--pdf", str(source), "--bilingual"],
    )

    assert result.exit_code == 0
    assert output.is_file()
    assert "PDF 已发布，但检测到以下质量问题" in result.output
    assert "page 1 font is too small" in result.output
