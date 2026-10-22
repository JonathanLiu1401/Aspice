"""Parser for Cadence More Info API documentation.

The More Info system consists of:
1. ``api_more_info.tgf`` — index mapping SKILL function names to (file, topic) pairs
2. HTML files containing the actual documentation, with topics delimited by
   ``<!-- [TOPIC_START_OPEN]... -->`` and ``<!-- [TOPIC_END] -->`` markers.
"""

from __future__ import annotations

import re
import markdownify
from dataclasses import dataclass
from pathlib import Path


# Pattern for .tgf index lines (whitespace-separated):
_TGF_QUOTED = re.compile(r"^(\S+)\s+(\S+)\s+\"([^\"]+)\"\s+(\S+)$")
_TGF_NULL = re.compile(r"^(\S+)\s+(\S+)\s+(NULL)\s+(\S+)$", re.IGNORECASE)

# Pattern for TOPIC_START block in HTML
_TOPIC_START = re.compile(r"<!--\s*\[TOPIC_START_OPEN\](.*?)-->", re.DOTALL)
_TOPIC_END = "<!-- [TOPIC_END] -->"
_TOPIC_TEXT = re.compile(r"\[TOPIC_START_ATTR\]text=([^\n]+)", re.IGNORECASE)


@dataclass
class MoreInfoEntry:
    func_name: str
    file_path: str
    topic: str | None
    format: str


@dataclass
class MoreInfoResult:
    func_name: str
    file_path: str
    topic: str | None
    raw_html: str
    plain_text: str


def parse_tgf_index(tgf_path: Path) -> dict[str, MoreInfoEntry]:
    entries: dict[str, MoreInfoEntry] = {}
    try:
        content = tgf_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return entries
    for line in content.splitlines():
        line = line.strip().strip("\r")
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        m = _TGF_QUOTED.match(line)
        if m:
            fn, fp, topic, fmt = m.group(1), m.group(2), m.group(3), m.group(4)
        else:
            m = _TGF_NULL.match(line)
            if not m:
                continue
            fn, fp, topic, fmt = m.group(1), m.group(2), m.group(3), m.group(4)
        if topic.upper() == "NULL":
            topic = None
        key = fn.lower()
        if key not in entries:
            entries[key] = MoreInfoEntry(func_name=fn, file_path=fp, topic=topic, format=fmt)
    return entries


def resolve_doc_path(tgf_path: Path, relative_path: str) -> Path:
    rel = relative_path.lstrip("$")
    return tgf_path.parent.parent / rel


def extract_topic_from_html(html_content: str, topic_name: str) -> str | None:
    for match in _TOPIC_START.finditer(html_content):
        block_start = match.start()
        block_attrs = match.group(1)
        text_match = _TOPIC_TEXT.search(block_attrs)
        if text_match and text_match.group(1).strip() == topic_name:
            end_pos = html_content.find(_TOPIC_END, block_start)
            if end_pos != -1:
                tag_end = html_content.find("-->", block_start)
                return html_content[tag_end + 3:end_pos].strip()
    return None


def html_to_plain_text(html: str) -> str:
    """Convert HTML to clean markdown via markdownify.

    Pre-processing removes empty code tags that would otherwise produce
    orphaned backticks in the markdown output.
    """
    if not html or not html.strip():
        return ""

    # Remove empty/self-closing code tags BEFORE conversion.
    # The Cadence HTML has malformed structures like:
    #   [<code>ExtractShapeLimit</code><code></code>]
    # which would produce orphaned backticks.
    html = re.sub(r"<code[^>]*/>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<code[^>]*></code>", "", html, flags=re.IGNORECASE)

    return markdownify.markdownify(
        html,
        heading_style="ATX",
        code_language="",
        bold_symbol="**",
        italic_symbol="_",
    ).strip()


def get_all_indexed_files(tgf_entries: dict[str, MoreInfoEntry]) -> set[str]:
    return {entry.file_path for entry in tgf_entries.values()}
