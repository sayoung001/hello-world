"""
Markdown → Notion 블록 변환 (A: 설계 문서를 Notion으로).

지원: 제목(#/##/###+), 불릿(-,*), 번호목록, 코드펜스(```), 인용(>), 구분선(---),
      표(| | |)는 코드블록으로 보존, 그 외는 단락.
Notion 제약: rich_text 2000자/블록, children 100블록/요청 → 호출측에서 청크.
"""

from __future__ import annotations

import re
from typing import Iterator

MAX_TEXT = 1900   # 안전 여유


def _rt(text: str) -> list:
    return [{"type": "text", "text": {"content": text[:MAX_TEXT]}}]


def _chunks(text: str, n: int = MAX_TEXT) -> Iterator[str]:
    for i in range(0, len(text), n):
        yield text[i:i + n]


def _para(text: str) -> list:
    # 2000자 초과 단락은 여러 블록으로 분할
    return [{"object": "block", "type": "paragraph",
             "paragraph": {"rich_text": _rt(c)}} for c in _chunks(text)] or \
           [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": []}}]


def _heading(text: str, level: int) -> dict:
    level = min(level, 3)
    return {"object": "block", "type": f"heading_{level}",
            f"heading_{level}": {"rich_text": _rt(text)}}


def _bullet(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": _rt(text)}}


def _numbered(text: str) -> dict:
    return {"object": "block", "type": "numbered_list_item",
            "numbered_list_item": {"rich_text": _rt(text)}}


def _quote(text: str) -> dict:
    return {"object": "block", "type": "quote", "quote": {"rich_text": _rt(text)}}


def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def _code(text: str, lang: str = "plain text") -> dict:
    return {"object": "block", "type": "code",
            "code": {"rich_text": _rt(text), "language": lang}}


def _strip_inline(text: str) -> str:
    """간단한 인라인 마크다운 제거(**, `, 링크 텍스트만 남김)."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text


def markdown_to_blocks(md: str) -> list[dict]:
    """Markdown 문자열 → Notion 블록 리스트."""
    blocks: list[dict] = []
    lines = md.splitlines()
    i = 0
    table_buf: list[str] = []

    def flush_table():
        nonlocal table_buf
        if table_buf:
            blocks.append(_code("\n".join(table_buf), "markdown"))
            table_buf = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 코드펜스
        if stripped.startswith("```"):
            flush_table()
            lang = stripped[3:].strip() or "plain text"
            buf = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i]); i += 1
            blocks.append(_code("\n".join(buf), _norm_lang(lang)))
            i += 1
            continue

        # 표(연속 | 행)
        if stripped.startswith("|") and stripped.endswith("|"):
            table_buf.append(stripped)
            i += 1
            continue
        else:
            flush_table()

        if not stripped:
            i += 1
            continue

        m = re.match(r"^(#{1,6})\s+(.*)", stripped)
        if m:
            blocks.append(_heading(_strip_inline(m.group(2)), len(m.group(1))))
        elif stripped in ("---", "***", "___"):
            blocks.append(_divider())
        elif re.match(r"^[-*]\s+", stripped):
            blocks.append(_bullet(_strip_inline(re.sub(r"^[-*]\s+", "", stripped))))
        elif re.match(r"^\d+\.\s+", stripped):
            blocks.append(_numbered(_strip_inline(re.sub(r"^\d+\.\s+", "", stripped))))
        elif stripped.startswith(">"):
            blocks.append(_quote(_strip_inline(stripped.lstrip("> ").strip())))
        else:
            blocks.extend(_para(_strip_inline(stripped)))
        i += 1

    flush_table()
    return blocks


_LANG_MAP = {"python": "python", "py": "python", "bash": "shell", "sh": "shell",
             "json": "json", "markdown": "markdown", "md": "markdown",
             "jsonc": "json", "text": "plain text"}


def _norm_lang(lang: str) -> str:
    return _LANG_MAP.get(lang.lower(), "plain text")


def batched(blocks: list[dict], size: int = 100) -> Iterator[list[dict]]:
    """Notion children 100개 제한 청크."""
    for i in range(0, len(blocks), size):
        yield blocks[i:i + size]
