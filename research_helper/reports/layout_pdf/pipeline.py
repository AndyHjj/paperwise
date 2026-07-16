from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import fitz

from research_helper import config
from research_helper.readers.arxiv_reader import PaperMeta
from research_helper.reports.layout_pdf.backend import (
    BabelDocBackendError,
    BabelDocConfigurationError,
    BabelDocProvider,
    translate_with_babeldoc,
)
from research_helper.reports.layout_pdf.composer import compose_side_by_side
from research_helper.reports.layout_pdf.cache_stamp import (
    record_translation_cache,
    translation_cache_matches,
)
from research_helper.reports.layout_pdf.dual_fidelity import (
    DualPdfStructureError,
    verify_dual_pdf,
)
from research_helper.reports.layout_pdf.fidelity import (
    PdfFidelityError,
    verify_translated_pdf,
)
from research_helper.reports.layout_pdf.quality_warnings import (
    QualityWarningSink,
    combine_warnings,
    notify_quality_warnings,
)
from research_helper.reports.layout_pdf.text_repair import repair_latin_typography
from research_helper.reports.layout_pdf.url_repair import repair_fragmented_urls

_PROVIDER_FIELDS = {
    "openai": ("OPENAI_API_KEY", "OPENAI_BASE_URL"),
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"),
    "qwen": ("QWEN_API_KEY", "QWEN_BASE_URL"),
    "mimo": ("MIMO_API_KEY", "MIMO_BASE_URL"),
}
_DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "deepseek": "deepseek-chat",
    "qwen": "qwen-plus",
    "mimo": "mimo-v2.5-pro",
}


def generate(
    source_pdf: Path,
    paper_dir: Path,
    meta: PaperMeta,
    force: bool = False,
    max_pages: int | None = None,
    warning_sink: QualityWarningSink | None = None,
) -> Path:
    del meta
    if max_pages is not None and max_pages < 1:
        raise BabelDocConfigurationError("max_pages", "must be at least 1")
    source_pdf = source_pdf.resolve()
    with fitz.open(source_pdf) as source:
        selected_pages = (
            min(source.page_count, max_pages)
            if max_pages is not None
            else source.page_count
        )
    paper_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = _safe_stem(source_pdf.stem)
    page_suffix = f"_pages_1-{selected_pages}" if max_pages is not None else ""
    output_path = paper_dir / (
        f"{safe_stem}_paperwise_bilingual_layout{page_suffix}.pdf"
    )
    artifact_dir = paper_dir / ".paperwise-layout"
    translated_pdf = artifact_dir / f"{source_pdf.stem}.no_watermark.zh.mono.pdf"
    translation_is_reusable = False
    quality_warning: str | None = None
    repair_count = 0
    if not force and translation_cache_matches(
        source_pdf,
        translated_pdf,
        selected_pages,
    ):
        translation_is_reusable, quality_warning = _assess_reusable_translation(
            source_pdf,
            translated_pdf,
            selected_pages,
        )
        if translation_is_reusable:
            repair_count = repair_fragmented_urls(source_pdf, translated_pdf)
            repair_count += repair_latin_typography(translated_pdf)
            if repair_count:
                quality_warning = _translated_pdf_warning(
                    source_pdf,
                    translated_pdf,
                    max_pages=selected_pages,
                )
                record_translation_cache(
                    source_pdf,
                    translated_pdf,
                    selected_pages,
                )
    if (
        output_path.exists()
        and not force
        and repair_count == 0
        and translation_is_reusable
    ):
        cached_dual_is_valid = False
        try:
            verify_dual_pdf(
                source_pdf,
                translated_pdf,
                output_path,
                max_pages=selected_pages,
            )
        except DualPdfStructureError:
            cached_dual_is_valid = False
        except PdfFidelityError as error:
            quality_warning = combine_warnings(quality_warning, str(error))
            cached_dual_is_valid = True
        except (OSError, RuntimeError, ValueError):
            cached_dual_is_valid = False
        else:
            cached_dual_is_valid = True
        if cached_dual_is_valid:
            notify_quality_warnings(quality_warning, warning_sink)
            return output_path

    if force or not translation_is_reusable:
        provider = provider_from_paperwise_config()
        translated_pdf = translate_with_babeldoc(
            source_pdf,
            artifact_dir,
            provider,
            max_pages=selected_pages,
            executable=_configured_executable(),
        )
        repair_fragmented_urls(source_pdf, translated_pdf)
        repair_latin_typography(translated_pdf)
        quality_warning = _translated_pdf_warning(
            source_pdf,
            translated_pdf,
            max_pages=selected_pages,
        )
        record_translation_cache(source_pdf, translated_pdf, selected_pages)

    with tempfile.NamedTemporaryFile(
        prefix=f".{output_path.stem}.",
        suffix=".staging.pdf",
        dir=paper_dir,
        delete=False,
    ) as temporary:
        staged_output = Path(temporary.name)
    staged_output.unlink()
    try:
        compose_side_by_side(
            source_pdf,
            translated_pdf,
            staged_output,
            max_pages=selected_pages,
        )
        try:
            verify_dual_pdf(
                source_pdf,
                translated_pdf,
                staged_output,
                max_pages=selected_pages,
            )
        except DualPdfStructureError:
            raise
        except PdfFidelityError as error:
            quality_warning = combine_warnings(quality_warning, str(error))
        staged_output.replace(output_path)
    finally:
        staged_output.unlink(missing_ok=True)
    notify_quality_warnings(quality_warning, warning_sink)
    return output_path


def provider_from_paperwise_config() -> BabelDocProvider:
    provider_name = str(config.LLM_PROVIDER).lower().strip()
    if provider_name == "anthropic":
        raise BabelDocBackendError(
            "The layout-preserving PDF backend requires an OpenAI-compatible provider. "
            "Select OpenAI, DeepSeek, Qwen, or MiMo; BabelDOC 0.6.3 cannot call the "
            "native Anthropic Messages API."
        )
    fields = _PROVIDER_FIELDS.get(provider_name)
    if fields is None:
        raise BabelDocBackendError(f"Unsupported PDF translation provider: {provider_name}")
    key_field, base_url_field = fields
    api_key = str(getattr(config, key_field, "")).strip()
    base_url = str(getattr(config, base_url_field, "")).strip()
    model = str(config.LLM_MODEL or _DEFAULT_MODELS[provider_name]).strip()
    if provider_name != "anthropic" and "claude" in model.lower():
        raise BabelDocBackendError(
            f"Model {model!r} does not match provider {provider_name!r}. "
            "Choose an explicit model in Paperwise settings."
        )
    return BabelDocProvider(
        model=model,
        base_url=base_url,
        api_key=api_key,
        qps=max(1, int(os.getenv("PAPERWISE_BABELDOC_QPS", "4"))),
        send_dashscope_header=(
            provider_name == "qwen" and "dashscope.aliyuncs.com" in base_url
        ),
    )


def _configured_executable() -> Path | None:
    value = os.getenv("PAPERWISE_BABELDOC_EXECUTABLE", "").strip()
    return Path(value).expanduser() if value else None


def _assess_reusable_translation(
    source_pdf: Path,
    translated_pdf: Path,
    max_pages: int | None,
) -> tuple[bool, str | None]:
    if not translated_pdf.is_file():
        return False, None
    try:
        quality_warning = _translated_pdf_warning(
            source_pdf,
            translated_pdf,
            max_pages=max_pages,
        )
        return True, quality_warning
    except (OSError, fitz.FileDataError):
        return False, None


def _translated_pdf_warning(
    source_pdf: Path,
    translated_pdf: Path,
    *,
    max_pages: int | None,
) -> str | None:
    try:
        verify_translated_pdf(source_pdf, translated_pdf, max_pages=max_pages)
    except PdfFidelityError as error:
        return str(error)
    return None


def _safe_stem(stem: str) -> str:
    return re.sub(r"[^\w.-]+", "_", stem).strip("._")[:80] or "paper"
