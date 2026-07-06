"""Strict full-paper translation for academic PDFs."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from research_helper import config
from research_helper.llm import client as llm
from research_helper.readers.arxiv_reader import PaperMeta
from research_helper.utils.cache import load_cache, save_cache

SYSTEM_PROMPT = """\
你是一个专业的学术论文翻译者。你的任务是严格翻译英文学术论文。

必须遵守：
1. 严格保持原文含义，不增加、不删减、不改写。
2. 这是逐段逐句翻译，不是总结、精读或评论。
3. 专业术语首次出现时，在中文后括号标注英文；后续可只用中文。
4. 保留所有数学公式、算法伪代码、数字数据、图表引用和参考文献编号。
5. 保持原文的段落结构和逻辑，不合并段落。
6. 人名、机构名保留英文原文。
7. 不添加任何译者注、总结、评价或额外标题。
"""

TRANSLATE_PROMPT = """\
请严格翻译以下论文章节。

章节标题：{title}

要求：
1. 严格保持原文含义，不增加、不删减、不改写。
2. 专业术语首次出现时在中文后括号标注英文。
3. 保留所有数学公式、算法伪代码、数字数据、图表引用。
4. 保持原文的段落结构和逻辑。
5. 不添加任何译者注、总结或评价。
6. 人名、机构名保留英文原文。

请翻译以下内容：
---
{content}
---
"""

TITLE_PROMPT = """\
请把下面的英文学术论文标题严格翻译成中文，只输出中文标题，不要解释。

{title}
"""

SECTION_KEYWORDS = (
    "Introduction|Related Work|Background|Preliminary|Preliminaries|Method|Methods|"
    "Methodology|Approach|System Design|Design|Architecture|Model|Framework|"
    "Pretraining|Training|Experiment|Experiments|Experimental Setup|Evaluation|"
    "Results|Analysis|Ablation|Discussion|Limitations|Conclusion|Conclusions|"
    "Implementation|Dataset|Datasets|Benchmark|Benchmarks"
)

SECTION_HEADING_RE = re.compile(
    rf"(?m)^(?P<title>"
    rf"Abstract|摘要|References|Bibliography|Acknowledg(?:e)?ments?|"
    rf"(?:\d{{1,2}}|[IVX]{{1,6}})\.?\s+"
    rf"(?!(?:Table|Figure|Fig\.|Algorithm)\b)"
    rf"(?:{SECTION_KEYWORDS})"
    rf"(?:[ A-Za-z0-9,;:\-–—/&()]{{0,90}})?"
    rf")\s*$"
)


@dataclass
class Section:
    index: int
    title: str
    content: str


def generate(
    paper_dir: Path,
    meta: PaperMeta,
    full_text: str,
    force: bool = False,
    jobs: int = 1,
    max_section_chars: int = 10_000,
) -> Path:
    """Translate a paper into Chinese and save it as Markdown."""
    report_path = paper_dir / "translation.md"
    if report_path.exists() and not force:
        return report_path

    sections = split_sections(full_text, max_section_chars=max_section_chars)
    cached = load_cache(paper_dir, "translation")
    if cached and not force and _cache_matches(cached, sections):
        translated_title = cached.get("translated_title") or meta.title
        translations = cached["translations"]
    else:
        translated_title = _translate_title(meta.title)
        translations = _translate_sections(sections, jobs=max(1, jobs))
        save_cache(
            paper_dir,
            "translation",
            {
                "section_count": len(sections),
                "section_titles": [s.title for s in sections],
                "translated_title": translated_title,
                "translations": translations,
            },
        )

    report = _render_markdown(meta, translated_title, sections, translations)
    paper_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    return report_path


def split_sections(text: str, max_section_chars: int = 10_000) -> list[Section]:
    """Split extracted PDF text into ordered sections, then split oversized sections."""
    text = _normalize_text(text)
    matches = list(SECTION_HEADING_RE.finditer(text))
    sections: list[Section] = []

    if not matches:
        sections = [Section(0, "全文", text)]
    else:
        if matches[0].start() > 300:
            preface = text[:matches[0].start()].strip()
            if preface:
                sections.append(Section(len(sections), "论文信息", preface))

        for i, match in enumerate(matches):
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            title = _clean_heading(match.group("title"))
            content = text[start:end].strip()
            if content:
                sections.append(Section(len(sections), title, content))

    expanded: list[Section] = []
    for section in sections:
        parts = _split_large_text(section.content, max_section_chars)
        if len(parts) == 1:
            expanded.append(Section(len(expanded), section.title, parts[0]))
        else:
            for i, part in enumerate(parts, 1):
                expanded.append(
                    Section(len(expanded), f"{section.title}（第 {i}/{len(parts)} 部分）", part)
                )
    return expanded


def _translate_title(title: str) -> str:
    if not title or title == "Unknown":
        return title
    return llm.complete(SYSTEM_PROMPT, TITLE_PROMPT.format(title=title), max_tokens=300).strip()


def _translate_sections(sections: list[Section], jobs: int) -> list[str]:
    translations = [""] * len(sections)

    def work(section: Section) -> tuple[int, str]:
        prompt = TRANSLATE_PROMPT.format(title=section.title, content=section.content)
        translated = llm.complete(SYSTEM_PROMPT, prompt, max_tokens=12000).strip()
        return section.index, translated

    if jobs <= 1 or len(sections) <= 1:
        for section in sections:
            idx, translated = work(section)
            translations[idx] = translated
        return translations

    with ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = [executor.submit(work, section) for section in sections]
        for future in as_completed(futures):
            idx, translated = future.result()
            translations[idx] = translated
    return translations


def _render_markdown(
    meta: PaperMeta,
    translated_title: str,
    sections: list[Section],
    translations: list[str],
) -> str:
    authors = "，".join(meta.authors[:8]) + ("等" if len(meta.authors) > 8 else "")
    lines = [
        f"# {translated_title or meta.title}",
        "",
        f"**原文标题**：{meta.title}",
        f"**作者**：{authors or 'Unknown'}",
        f"**发表时间**：{meta.published or 'N/A'}",
        f"**Arxiv ID**：{meta.arxiv_id or 'local'}",
        "",
        "---",
        "",
    ]

    for section, translation in zip(sections, translations):
        lines.extend([f"## {section.title}", "", translation.strip(), "", "---", ""])
    return "\n".join(lines).rstrip() + "\n"


def _cache_matches(cached: dict, sections: list[Section]) -> bool:
    return (
        cached.get("section_count") == len(sections)
        and cached.get("section_titles") == [s.title for s in sections]
        and len(cached.get("translations", [])) == len(sections)
    )


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def _clean_heading(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip()


def _split_large_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    paragraphs = re.split(r"\n\s*\n", text)
    parts: list[str] = []
    current: list[str] = []
    current_len = 0

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) > max_chars:
            if current:
                parts.append("\n\n".join(current))
                current = []
                current_len = 0
            parts.extend(_split_hard(paragraph, max_chars))
            continue
        projected = current_len + len(paragraph) + (2 if current else 0)
        if current and projected > max_chars:
            parts.append("\n\n".join(current))
            current = [paragraph]
            current_len = len(paragraph)
        else:
            current.append(paragraph)
            current_len = projected

    if current:
        parts.append("\n\n".join(current))
    return parts or [text]


def _split_hard(text: str, max_chars: int) -> list[str]:
    return [text[i:i + max_chars] for i in range(0, len(text), max_chars)]
