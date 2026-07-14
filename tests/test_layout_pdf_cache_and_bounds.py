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
from research_helper.reports.layout_pdf import pipeline
from research_helper.reports.layout_pdf.backend import (
    BabelDocConfigurationError,
    BabelDocProvider,
)
from research_helper.reports.layout_pdf.fidelity import PdfFidelityError
from research_helper.reports.layout_pdf.dual_fidelity import DualPdfStructureError


def _write_pdf(path: Path, text: str, *, pages: int = 1) -> Path:
    document = fitz.open()
    for index in range(pages):
        page = document.new_page(width=220, height=300)
        content = f"{text} page {index + 1} token 42"
        font_name = (
            "china-s"
            if any("\u3400" <= char <= "\u9fff" for char in content)
            else "helv"
        )
        page.insert_text((20, 30), content, fontname=font_name, fontsize=8)
    document.save(path)
    document.close()
    return path


def _meta() -> PaperMeta:
    return PaperMeta("test", "test", [], "", "", "", [])


def _provider() -> BabelDocProvider:
    return BabelDocProvider("test-model", "http://127.0.0.1:9", "placeholder")


def _fake_translator(calls: list[int]) -> Callable[..., Path]:
    def translate(
        source_pdf: Path,
        artifact_dir: Path,
        provider: BabelDocProvider,
        *,
        max_pages: int | None = None,
        executable: Path | None = None,
    ) -> Path:
        del provider, executable
        calls.append(max_pages or 0)
        with fitz.open(source_pdf) as source:
            selected = min(source.page_count, max_pages) if max_pages else source.page_count
        output = artifact_dir / f"{source_pdf.stem}.no_watermark.zh.mono.pdf"
        output.parent.mkdir(parents=True, exist_ok=True)
        return _write_pdf(output, "中文译文", pages=selected)

    return translate


@pytest.mark.parametrize("value", ["0", "-1"])
def test_cli_rejects_nonpositive_max_pages_before_writing_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    source = _write_pdf(tmp_path / "source.pdf", "source")
    output_root = tmp_path / "outputs"
    monkeypatch.setattr(config, "OUTPUTS_DIR", output_root)
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "placeholder")

    result = CliRunner().invoke(
        main,
        ["translate", "--pdf", str(source), "--bilingual", "--max-pages", value],
    )

    assert result.exit_code == 2
    assert "Invalid value for '--max-pages'" in result.output
    assert not output_root.exists()


def test_configuration_error_allows_traceback_assignment() -> None:
    error = BabelDocConfigurationError("max_pages", "must be at least 1")

    error.__traceback__ = None

    assert str(error) == "invalid BabelDOC max_pages: must be at least 1"


def test_force_replaces_a_corrupt_cached_translation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_pdf(tmp_path / "source.pdf", "source")
    paper_dir = tmp_path / "paper"
    corrupt = paper_dir / ".paperwise-layout" / "source.no_watermark.zh.mono.pdf"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_bytes(b"not a pdf")
    calls: list[int] = []
    monkeypatch.setattr(pipeline, "provider_from_paperwise_config", _provider)
    monkeypatch.setattr(pipeline, "translate_with_babeldoc", _fake_translator(calls))

    result = layout_pdf.generate(source, paper_dir, _meta(), force=True)

    assert result.is_file()
    assert calls == [1]


def test_source_change_invalidates_the_cached_translation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_pdf(tmp_path / "source.pdf", "first source")
    paper_dir = tmp_path / "paper"
    calls: list[int] = []
    monkeypatch.setattr(pipeline, "provider_from_paperwise_config", _provider)
    monkeypatch.setattr(pipeline, "translate_with_babeldoc", _fake_translator(calls))
    layout_pdf.generate(source, paper_dir, _meta(), force=True)
    replacement = _write_pdf(tmp_path / "replacement.pdf", "changed source")
    replacement.replace(source)

    layout_pdf.generate(source, paper_dir, _meta())

    assert calls == [1, 1]


def test_max_pages_is_a_cap_not_an_exact_requested_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_pdf(tmp_path / "source.pdf", "source", pages=2)
    paper_dir = tmp_path / "paper"
    calls: list[int] = []
    monkeypatch.setattr(pipeline, "provider_from_paperwise_config", _provider)
    monkeypatch.setattr(pipeline, "translate_with_babeldoc", _fake_translator(calls))

    result = layout_pdf.generate(source, paper_dir, _meta(), force=True, max_pages=99)

    assert calls == [2]
    assert result.name.endswith("_pages_1-2.pdf")


def test_quality_gate_failure_still_publishes_with_a_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_pdf(tmp_path / "source.pdf", "source")
    paper_dir = tmp_path / "paper"
    calls: list[int] = []
    monkeypatch.setattr(pipeline, "provider_from_paperwise_config", _provider)
    monkeypatch.setattr(pipeline, "translate_with_babeldoc", _fake_translator(calls))

    def reject_quality(*_args: object, **_kwargs: object) -> None:
        raise PdfFidelityError(
            "PDF fidelity check failed:\n- page 1 introduced overlapping body text"
        )

    monkeypatch.setattr(pipeline, "verify_translated_pdf", reject_quality)

    with pytest.warns(UserWarning, match="page 1 introduced overlapping body text"):
        result = layout_pdf.generate(source, paper_dir, _meta(), force=True)

    assert result.is_file()
    assert calls == [1]


def test_dual_gate_failure_still_publishes_with_a_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_pdf(tmp_path / "source.pdf", "source")
    paper_dir = tmp_path / "paper"
    calls: list[int] = []
    monkeypatch.setattr(pipeline, "provider_from_paperwise_config", _provider)
    monkeypatch.setattr(pipeline, "translate_with_babeldoc", _fake_translator(calls))

    def reject_dual(*_args: object, **_kwargs: object) -> None:
        raise PdfFidelityError(
            "Dual PDF fidelity check failed:\n- page 1 link annotations changed"
        )

    monkeypatch.setattr(pipeline, "verify_dual_pdf", reject_dual)

    with pytest.warns(UserWarning, match="page 1 link annotations changed"):
        result = layout_pdf.generate(source, paper_dir, _meta(), force=True)

    assert result.is_file()
    assert calls == [1]


def test_quality_warnings_can_be_delivered_to_a_caller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_pdf(tmp_path / "source.pdf", "source")
    paper_dir = tmp_path / "paper"
    calls: list[int] = []
    delivered: list[str] = []
    monkeypatch.setattr(pipeline, "provider_from_paperwise_config", _provider)
    monkeypatch.setattr(pipeline, "translate_with_babeldoc", _fake_translator(calls))

    def reject_quality(*_args: object, **_kwargs: object) -> None:
        raise PdfFidelityError("PDF fidelity check failed:\n- page 1 font is too small")

    monkeypatch.setattr(pipeline, "verify_translated_pdf", reject_quality)

    result = layout_pdf.generate(
        source,
        paper_dir,
        _meta(),
        force=True,
        warning_sink=delivered.extend,
    )

    assert result.is_file()
    assert delivered == ["PDF fidelity check failed:\n- page 1 font is too small"]


def test_structural_dual_failure_does_not_replace_the_formal_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_pdf(tmp_path / "source.pdf", "source")
    paper_dir = tmp_path / "paper"
    formal = paper_dir / "source_paperwise_bilingual_layout.pdf"
    formal.parent.mkdir(parents=True)
    formal.write_bytes(b"previous-good-output")
    calls: list[int] = []
    monkeypatch.setattr(pipeline, "provider_from_paperwise_config", _provider)
    monkeypatch.setattr(pipeline, "translate_with_babeldoc", _fake_translator(calls))

    def reject_structure(*_args: object, **_kwargs: object) -> None:
        raise DualPdfStructureError("dual page count mismatch")

    monkeypatch.setattr(pipeline, "verify_dual_pdf", reject_structure)

    with pytest.raises(DualPdfStructureError, match="page count"):
        layout_pdf.generate(source, paper_dir, _meta(), force=True)

    assert formal.read_bytes() == b"previous-good-output"
