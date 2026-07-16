"""Tests for research_helper.reports.translator."""
from __future__ import annotations

import pytest
from research_helper.reports.translator import (
    Section,
    split_sections,
    _normalize_text,
    _clean_heading,
    _split_large_text,
    _split_hard,
    _cache_matches,
    SECTION_HEADING_RE,
)


class TestNormalizeText:
    def test_normalize_line_endings(self):
        assert _normalize_text("a\r\nb\r\nc") == "a\nb\nc"
        assert _normalize_text("a\rb\rc") == "a\nb\nc"

    def test_collapse_trailing_spaces(self):
        assert _normalize_text("a  \nb") == "a\nb"

    def test_collapse_excessive_newlines(self):
        assert _normalize_text("a\n\n\n\nb") == "a\n\n\nb"

    def test_strips_whitespace(self):
        assert _normalize_text("  hello  ") == "hello"


class TestCleanHeading:
    def test_collapses_spaces(self):
        assert _clean_heading("Introduction   and    Background") == "Introduction and Background"

    def test_strips_whitespace(self):
        assert _clean_heading("  Method  ") == "Method"


class TestSplitHard:
    def test_smaller_than_max(self):
        assert _split_hard("hello", 100) == ["hello"]

    def test_split_by_chars(self):
        result = _split_hard("abcdefghij", 4)
        assert result == ["abcd", "efgh", "ij"]


class TestSplitLargeText:
    def test_under_max_returns_single(self):
        result = _split_large_text("short text", 100)
        assert result == ["short text"]

    def test_split_by_paragraphs(self):
        text = "A" * 50 + "\n\n" + "B" * 50 + "\n\n" + "C" * 50
        result = _split_large_text(text, 80)
        assert len(result) >= 2
        assert all(len(p) <= 80 for p in result)

    def test_hard_split_giant_paragraph(self):
        text = "A" * 1000
        result = _split_large_text(text, 300)
        assert len(result) >= 3
        assert all(len(p) <= 300 for p in result)


class TestSectionHeadingRegex:
    def test_matches_abstract(self):
        assert SECTION_HEADING_RE.search("Abstract")

    def test_matches_numbered_section(self):
        assert SECTION_HEADING_RE.search("1 Introduction")
        assert SECTION_HEADING_RE.search("I. Background")

    def test_does_not_match_table_figure(self):
        assert not SECTION_HEADING_RE.search("1. Table 1: Results")
        assert not SECTION_HEADING_RE.search("2. Figure 3: Architecture")

    def test_does_not_match_algorithm(self):
        assert not SECTION_HEADING_RE.search("1. Algorithm 1: Training")

    def test_matches_related_work(self):
        assert SECTION_HEADING_RE.search("2 Related Work")
        assert SECTION_HEADING_RE.search("II. Related Work")


class TestSplitSections:
    def test_no_headings_returns_full_text(self):
        text = "Just some plain text without any section headings."
        sections = split_sections(text)
        assert len(sections) == 1
        assert sections[0].title == "全文"

    def test_extracts_sections_by_headings(self):
        text = (
            "This is the abstract text.\n\n"
            "1 Introduction\n"
            "This is the introduction.\n\n"
            "2 Method\n"
            "This is the method section.\n\n"
            "3 Conclusion\n"
            "Final remarks."
        )
        sections = split_sections(text, max_section_chars=5000)
        titles = [s.title for s in sections]
        assert "1 Introduction" in str(titles) or "Introduction" in str(titles)

    def test_preface_before_first_heading(self):
        text = ("A" * 350) + "\n\n" + "1 Introduction\n" + "Body text."
        sections = split_sections(text, max_section_chars=5000)
        assert any("论文信息" in s.title for s in sections)

    def test_max_section_chars_triggers_split(self):
        body = "A" * 2000 + "\n\n" + "B" * 2000
        text = "1 Introduction\n" + body
        sections = split_sections(text, max_section_chars=1000)
        assert len(sections) >= 2


class TestCacheMatches:
    def make_cached(self, n, titles, translations_n=None):
        return {
            "section_count": n,
            "section_titles": titles,
            "translations": [""] * (translations_n if translations_n is not None else n),
        }

    def make_sections(self, titles):
        return [Section(i, t, "content") for i, t in enumerate(titles)]

    def test_exact_match(self):
        cached = self.make_cached(2, ["A", "B"])
        sections = self.make_sections(["A", "B"])
        assert _cache_matches(cached, sections)

    def test_wrong_count(self):
        cached = self.make_cached(2, ["A", "B"])
        sections = self.make_sections(["A"])
        assert not _cache_matches(cached, sections)

    def test_wrong_titles(self):
        cached = self.make_cached(2, ["A", "B"])
        sections = self.make_sections(["A", "C"])
        assert not _cache_matches(cached, sections)

    def test_mismatched_translation_length(self):
        cached = self.make_cached(2, ["A", "B"], translations_n=1)
        sections = self.make_sections(["A", "B"])
        assert not _cache_matches(cached, sections)
