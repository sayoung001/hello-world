"""
Notion 게시 — 배치 정리 내용 (사용자 정책: 배치=Notion, 실시간=Telegram).

게시 대상(설계 §5):
  - '오늘의 추천' DB: 종목·시장·점수·보유기간·진입조건·손절·목표·예상손익·섹터상태
  - 일자 요약 페이지: 매크로 레짐 + 섹터 현황

Notion REST API(requests) 직접 사용 — 토큰은 NOTION_TOKEN 환경변수.
payload 빌더는 순수 함수로 분리해 네트워크 없이 검증 가능.

setup: create_recommendations_db(parent_page_id) 1회 → DB ID 저장 후 매일 add.
"""

from __future__ import annotations

import os
from typing import Any, Optional

NOTION_VERSION = "2022-06-28"
NOTION_BASE = "https://api.notion.com/v1"


# ── payload 빌더 (순수 함수) ────────────────────────────────────────────────
def recommendations_db_schema(title: str = "오늘의 추천") -> dict:
    """'오늘의 추천' DB 속성 스키마."""
    return {
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": {
            "종목": {"title": {}},
            "시장": {"select": {"options": [
                {"name": "US", "color": "blue"}, {"name": "KR", "color": "red"}]}},
            "추천": {"select": {"options": [
                {"name": "BUY", "color": "green"}, {"name": "WATCH", "color": "yellow"},
                {"name": "AVOID", "color": "gray"}]}},
            "보유기간": {"select": {"options": [
                {"name": "단타"}, {"name": "중단기"}, {"name": "스윙"}]}},
            "Effective": {"number": {"format": "number"}},
            "확신도": {"number": {"format": "percent"}},
            "진입조건": {"rich_text": {}},
            "손절": {"number": {"format": "number"}},
            "목표": {"rich_text": {}},
            "예상손익P10": {"number": {"format": "number"}},
            "예상손익P50": {"number": {"format": "number"}},
            "예상손익P90": {"number": {"format": "number"}},
            "섹터상태": {"rich_text": {}},
            "일자": {"date": {}},
        },
    }


def _rt(text: str) -> list:
    return [{"type": "text", "text": {"content": str(text)[:1900]}}]


def recommendation_row(reco: dict, market: str, date: str,
                       sector_label: str = "-") -> dict:
    """추천 dict → Notion 페이지(행) properties."""
    pnl = reco.get("expected_pnl_today", {}) or {}
    targets = reco.get("targets", []) or []
    return {
        "종목": {"title": _rt(reco.get("ticker", "-"))},
        "시장": {"select": {"name": market}},
        "추천": {"select": {"name": reco.get("recommend", "WATCH")}},
        "보유기간": {"select": {"name": reco.get("horizon", "중단기")}},
        "Effective": {"number": _num(reco.get("effective_score"))},
        "확신도": {"number": _num(reco.get("conviction"))},
        "진입조건": {"rich_text": _rt(reco.get("entry_rule", ""))},
        "손절": {"number": _num(reco.get("stop"))},
        "목표": {"rich_text": _rt(", ".join(str(t) for t in targets))},
        "예상손익P10": {"number": _num(pnl.get("p10_pct"))},
        "예상손익P50": {"number": _num(pnl.get("p50_pct"))},
        "예상손익P90": {"number": _num(pnl.get("p90_pct"))},
        "섹터상태": {"rich_text": _rt(sector_label)},
        "일자": {"date": {"start": date}},
    }


def _num(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def daily_summary_blocks(macro_label: str, macro_level: int,
                         sector_lines: list[str], disclaimer: bool = True) -> list:
    """일자 요약 페이지 본문 블록."""
    blocks = [
        _heading(f"매크로 레짐: {macro_label} (L{macro_level})"),
        _heading("섹터 현황", level=3),
    ]
    for line in sector_lines:
        blocks.append(_bullet(line))
    if disclaimer:
        blocks.append(_callout(
            "본 정보는 분석 보조용이며 투자 권유가 아닙니다. 투자 책임은 본인에게 있습니다."))
    return blocks


def _heading(text: str, level: int = 2) -> dict:
    return {"object": "block", "type": f"heading_{level}",
            f"heading_{level}": {"rich_text": _rt(text)}}


def _bullet(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": _rt(text)}}


def _callout(text: str) -> dict:
    return {"object": "block", "type": "callout",
            "callout": {"rich_text": _rt(text), "icon": {"emoji": "⚠️"}}}


# ── REST 클라이언트 ─────────────────────────────────────────────────────────
class NotionPublisher:
    def __init__(self, token: Optional[str] = None):
        self.token = token or os.environ.get("NOTION_TOKEN", "")

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json"}

    def _post(self, path: str, payload: dict) -> Optional[dict]:
        if not self.enabled:
            print(f"[notion] 미설정 — {path} 생략")
            return None
        try:
            import requests
            r = requests.post(f"{NOTION_BASE}{path}", headers=self._headers(),
                              json=payload, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            print(f"[notion] {path} 실패: {type(e).__name__}: {e}")
            return None

    def _patch(self, path: str, payload: dict) -> Optional[dict]:
        if not self.enabled:
            print(f"[notion] 미설정 — {path} 생략")
            return None
        try:
            import requests
            r = requests.patch(f"{NOTION_BASE}{path}", headers=self._headers(),
                               json=payload, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            print(f"[notion] {path} 실패: {type(e).__name__}: {e}")
            return None

    def create_recommendations_db(self, parent_page_id: str,
                                  title: str = "오늘의 추천") -> Optional[str]:
        """1회 셋업: 부모 페이지 아래 추천 DB 생성 → DB ID 반환."""
        schema = recommendations_db_schema(title)
        payload = {"parent": {"type": "page_id", "page_id": parent_page_id}, **schema}
        res = self._post("/databases", payload)
        return res.get("id") if res else None

    def add_recommendation(self, db_id: str, reco: dict, market: str,
                           date: str, sector_label: str = "-") -> Optional[str]:
        props = recommendation_row(reco, market, date, sector_label)
        res = self._post("/pages", {"parent": {"database_id": db_id},
                                    "properties": props})
        return res.get("id") if res else None

    def create_summary_page(self, parent_page_id: str, title: str,
                            blocks: list) -> Optional[str]:
        payload = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "properties": {"title": {"title": _rt(title)}},
            "children": blocks[:100],   # 생성 시 100블록 제한
        }
        res = self._post("/pages", payload)
        page_id = res.get("id") if res else None
        if page_id and len(blocks) > 100:
            self.append_blocks(page_id, blocks[100:])
        return page_id

    def append_blocks(self, block_id: str, blocks: list) -> None:
        """100개씩 청크로 자식 블록 추가."""
        from stock_auto.integrations.md_to_notion import batched
        for chunk in batched(blocks, 100):
            self._patch(f"/blocks/{block_id}/children", {"children": chunk})

    def create_page_from_markdown(self, parent_page_id: str, title: str,
                                  md_text: str) -> Optional[str]:
        """Markdown 문서 → Notion 페이지(A: 설계 문서 변환)."""
        from stock_auto.integrations.md_to_notion import markdown_to_blocks
        blocks = markdown_to_blocks(md_text)
        return self.create_summary_page(parent_page_id, title, blocks)
