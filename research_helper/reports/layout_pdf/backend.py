from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import fitz

BABELDOC_VERSION = "0.6.3"
_EXECUTABLE_ENV = "PAPERWISE_BABELDOC_EXECUTABLE"
_TRANSLATION_PROMPT = (
    "Translate into concise Simplified Chinese. Preserve every URL, number, formula, "
    "citation marker, and placeholder exactly. Do not expand or summarize; keep the "
    "translation close to the source length so it fits the original layout."
)


class BabelDocBackendError(RuntimeError):
    pass


class BabelDocConfigurationError(BabelDocBackendError):
    __slots__ = ("field", "reason")

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(field, reason)
        self.field = field
        self.reason = reason

    def __str__(self) -> str:
        return f"invalid BabelDOC {self.field}: {self.reason}"


@dataclass(frozen=True, slots=True)
class BabelDocProvider:
    model: str
    base_url: str
    api_key: str
    qps: int = 4
    send_dashscope_header: bool = False

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise BabelDocConfigurationError("model", "a non-empty name is required")
        if not self.api_key.strip():
            raise BabelDocConfigurationError("api_key", "a non-empty key is required")
        if self.qps < 1:
            raise BabelDocConfigurationError("qps", "must be at least 1")


def build_babeldoc_command(
    executable: Path,
    source_pdf: Path,
    config_path: Path,
) -> list[str]:
    return [
        str(executable),
        "--config",
        str(config_path),
        "--files",
        str(source_pdf),
    ]


def render_babeldoc_config(
    provider: BabelDocProvider,
    output_dir: Path,
    working_dir: Path,
    max_pages: int | None,
) -> str:
    if max_pages is not None and max_pages < 1:
        raise BabelDocConfigurationError("max_pages", "must be at least 1")

    values: list[tuple[str, str]] = [
        ("lang-in", _toml_string("en")),
        ("lang-out", _toml_string("zh")),
        ("output", _toml_string(str(output_dir.resolve()))),
        ("working-dir", _toml_string(str(working_dir.resolve()))),
        ("qps", str(provider.qps)),
        ("pool-max-workers", str(provider.qps)),
        ("report-interval", "0.1"),
        ("openai", "true"),
        ("openai-model", _toml_string(provider.model)),
        ("openai-api-key", _toml_string(provider.api_key)),
        ("no-dual", "true"),
        ("no-mono", "false"),
        ("watermark-output-mode", _toml_string("no_watermark")),
        ("no-auto-extract-glossary", "true"),
        ("custom-system-prompt", _toml_string(_TRANSLATION_PROMPT)),
    ]
    if provider.base_url.strip():
        values.append(("openai-base-url", _toml_string(provider.base_url)))
    if provider.send_dashscope_header:
        values.append(("send-dashscope-header", "true"))
    if max_pages is not None:
        values.extend(
            [
                ("pages", _toml_string(f"1-{max_pages}")),
                ("only-include-translated-page", "true"),
            ]
        )
    body = "\n".join(f"{key} = {value}" for key, value in values)
    return f"[babeldoc]\n{body}\n"


def locate_babeldoc_executable(explicit: Path | None = None) -> Path:
    candidates: list[Path] = []
    configured = explicit or _path_from_env(_EXECUTABLE_ENV)
    if configured is not None:
        candidates.append(configured)
    on_path = shutil.which("babeldoc")
    if on_path:
        candidates.append(Path(on_path))
    repository = Path(__file__).resolve().parents[3]
    executable_name = "babeldoc.exe" if os.name == "nt" else "babeldoc"
    candidates.extend(
        [
            repository
            / ".paperwise-runtime"
            / "babeldoc-v063"
            / ("Scripts" if os.name == "nt" else "bin")
            / executable_name,
        ]
    )
    uv_bin = _uv_tool_bin()
    if uv_bin is not None:
        candidates.append(uv_bin / executable_name)

    for candidate in candidates:
        if candidate.is_file():
            require_babeldoc_version(candidate)
            return candidate.resolve()
    raise BabelDocBackendError(
        "BabelDOC 0.6.3 was not found. Install it in a separate environment with "
        "`uv tool install --python 3.12 \"BabelDOC==0.6.3\"`, or set "
        f"{_EXECUTABLE_ENV}."
    )


@lru_cache(maxsize=8)
def require_babeldoc_version(executable: Path) -> None:
    result = _run_process([str(executable), "--version"], timeout=60)
    version_text = f"{result.stdout}\n{result.stderr}".strip()
    if result.returncode != 0 or f"babeldoc {BABELDOC_VERSION}" not in version_text:
        raise BabelDocBackendError(
            f"Expected BabelDOC {BABELDOC_VERSION}, got: {version_text or 'no version output'}"
        )


def translate_with_babeldoc(
    source_pdf: Path,
    artifact_dir: Path,
    provider: BabelDocProvider,
    *,
    max_pages: int | None = None,
    executable: Path | None = None,
    timeout_seconds: int = 7_200,
) -> Path:
    source_pdf = source_pdf.resolve()
    if not source_pdf.is_file():
        raise FileNotFoundError(source_pdf)
    with fitz.open(source_pdf) as source:
        selected_pages = (
            min(source.page_count, max_pages)
            if max_pages is not None
            else source.page_count
        )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    backend = locate_babeldoc_executable(executable)
    final_path = artifact_dir / f"{source_pdf.stem}.no_watermark.zh.mono.pdf"

    with tempfile.TemporaryDirectory(prefix="paperwise-babeldoc-", dir=artifact_dir) as temp:
        job_dir = Path(temp)
        output_dir = job_dir / "output"
        working_dir = job_dir / "working"
        output_dir.mkdir()
        working_dir.mkdir()
        config_path = job_dir / "backend.toml"
        config_path.write_text(
            render_babeldoc_config(
                provider,
                output_dir,
                working_dir,
                selected_pages,
            ),
            encoding="utf-8",
        )
        try:
            command = build_babeldoc_command(backend, source_pdf, config_path)
            result = _run_process(command, timeout=timeout_seconds)
            expected = output_dir / final_path.name
            _require_output(result, expected, provider.api_key, selected_pages)
            shutil.copy2(expected, final_path)
        finally:
            config_path.unlink(missing_ok=True)
    return final_path


def _require_output(
    result: subprocess.CompletedProcess[str],
    expected: Path,
    secret: str,
    expected_pages: int,
) -> None:
    error = ""
    if result.returncode != 0:
        error = f"exit code {result.returncode}"
    elif not expected.is_file() or expected.stat().st_size == 0:
        error = "the expected mono PDF was not created"
    else:
        try:
            with fitz.open(expected) as document:
                if document.page_count < 1:
                    error = "the generated mono PDF has no pages"
                elif document.page_count != expected_pages:
                    error = (
                        f"the generated mono PDF has {document.page_count} pages; "
                        f"expected {expected_pages}"
                    )
        except (OSError, RuntimeError, ValueError) as exc:
            error = f"the generated mono PDF cannot be opened: {exc}"
    if error:
        logs = _redact(f"{result.stdout}\n{result.stderr}"[-8_000:], secret)
        raise BabelDocBackendError(f"BabelDOC failed: {error}\n{logs}".strip())


def _run_process(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired as exc:
        raise BabelDocBackendError(f"BabelDOC timed out after {timeout} seconds") from exc


def _uv_tool_bin() -> Path | None:
    uv = shutil.which("uv")
    if not uv:
        return None
    result = _run_process([uv, "tool", "dir", "--bin"], timeout=30)
    path = result.stdout.strip()
    return Path(path) if result.returncode == 0 and path else None


def _path_from_env(name: str) -> Path | None:
    value = os.getenv(name, "").strip()
    return Path(value).expanduser() if value else None


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "********") if secret else text
