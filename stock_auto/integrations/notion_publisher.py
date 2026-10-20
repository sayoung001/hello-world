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

NOTION_VERSION = "2025-09-03"
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


SECTOR_SCORE_LABEL = {5: "강력매수", 4: "상승추세", 3: "회복중",
                      2: "보합", 1: "하락초기", 0: "하락위험"}


def recommendation_row(reco: dict, market: str, date: str,
                       sector_label: str = "-",
                       row: Optional[dict] = None) -> dict:
    """
    추천 dict → Notion 페이지(행) properties.

    ★ row(screener 행)를 함께 받아야 하는 이유 — 세 가지 값이 reco에 없다.
      1) effective_score: RECO_SCHEMA의 properties에 아예 없는 키다. LLM이
         반환할 수 없으므로 reco.get("effective_score")는 **항상 None**이었고,
         Notion 'Effective' 컬럼이 전 건 비어 있었다(실측 76/76건 누락).
         점수는 규칙엔진이 계산하므로 출처는 row다.
      2) 종목: LLM이 ticker를 틀리게 쓰면 그대로 제목이 된다. 실제로 "KR",
         "KR (US)" 같은 행이 생겼다. row["symbol"]이 정답이다.
      3) 섹터상태: sector_label_map에서 못 찾으면 "-"가 된다(실측 19/76건).
         row에 sector_etf·sector_status_score가 있으므로 그걸로 복원한다.
    """
    row = row or {}
    pnl = reco.get("expected_pnl_today", {}) or {}
    targets = reco.get("targets", []) or []

    # 규칙엔진 값 우선, 없으면 reco 폴백
    eff = _num(row.get("effective_score"))
    if eff is None:
        eff = _num(reco.get("effective_score"))
    symbol = str(row.get("symbol") or reco.get("ticker") or "-")
    stop = _num(reco.get("stop"))
    if stop is None:
        stop = _num(row.get("stop"))
    target_txt = ", ".join(str(t) for t in targets)
    if not target_txt and row.get("target") is not None:
        target_txt = str(row.get("target"))

    return {
        "종목": {"title": _rt(symbol)},
        "시장": {"select": {"name": market}},
        "추천": {"select": {"name": reco.get("recommend") or "WATCH"}},
        "보유기간": {"select": {"name": reco.get("horizon") or "중단기"}},
        "Effective": {"number": eff},
        "확신도": {"number": _num(reco.get("conviction"))},
        "진입조건": {"rich_text": _rt(reco.get("entry_rule", ""))},
        "손절": {"number": stop},
        "목표": {"rich_text": _rt(target_txt)},
        "예상손익P10": {"number": _num(pnl.get("p10_pct"))},
        "예상손익P50": {"number": _num(pnl.get("p50_pct"))},
        "예상손익P90": {"number": _num(pnl.get("p90_pct"))},
        "섹터상태": {"rich_text": _rt(_sector_text(sector_label, row))},
        "일자": {"date": {"start": date}},
    }


def _sector_text(sector_label: str, row: dict) -> str:
    """섹터 라벨이 비었으면 screener 행의 ETF·상태점수로 복원."""
    if sector_label and sector_label not in ("-", "—"):
        return sector_label
    etf = row.get("sector_etf")
    score = row.get("sector_status_score")
    if etf and etf != "-":
        if score is not None and score == score:      # NaN 아님
            s = int(score)
            return f"{etf} {SECTOR_SCORE_LABEL.get(s, '?')} [{s}]"
        return f"{etf} (상태 미상)"
    return "-"


def _num(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def daily_summary_blocks(macro_label: str, macro_level: int,
                         score_lines: list[str],
                         sector_lines: Optional[list[str]] = None,
                         exit_lines: Optional[list[str]] = None,
                         disclaimer: bool = True) -> list:
    """일자 요약 페이지 본문 블록."""
    blocks = [_heading(f"매크로 레짐: {macro_label} (L{macro_level})")]
    if sector_lines:
        blocks.append(_heading("섹터 현황", level=3))
        for line in sector_lines:
            blocks.append(_bullet(line))
    blocks.append(_heading("종목 점수 상위", level=3))
    for line in score_lines:
        blocks.append(_bullet(line))
    if exit_lines:
        blocks.append(_heading("매도 신호 — 보유 시 청산 점검", level=3))
        for line in exit_lines:
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
        r = None
        try:
            import requests
            r = requests.post(f"{NOTION_BASE}{path}", headers=self._headers(),
                              json=payload, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            body = ""
            if r is not None:
                try:
                    body = f" | resp: {r.text[:500]}"
                except Exception:  # noqa: BLE001
                    body = ""
            print(f"[notion] {path} 실패: {type(e).__name__}: {e}{body}")
            return None

    def _patch(self, path: str, payload: dict) -> Optional[dict]:
        if not self.enabled:
            print(f"[notion] 미설정 — {path} 생략")
            return None
        r = None
        try:
            import requests
            r = requests.patch(f"{NOTION_BASE}{path}", headers=self._headers(),
                               json=payload, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            body = ""
            if r is not None:
                try:
                    body = f" | resp: {r.text[:500]}"
                except Exception:  # noqa: BLE001
                    body = ""
            print(f"[notion] {path} 실패: {type(e).__name__}: {e}{body}")
            return None

    def _get(self, path: str, params: Optional[dict] = None) -> Optional[dict]:
        if not self.enabled:
            print(f"[notion] 미설정 — {path} 생략")
            return None
        r = None
        try:
            import requests
            r = requests.get(f"{NOTION_BASE}{path}", headers=self._headers(),
                             params=params or {}, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            body = ""
            if r is not None:
                try:
                    body = f" | resp: {r.text[:500]}"
                except Exception:  # noqa: BLE001
                    body = ""
            print(f"[notion] {path} 실패: {type(e).__name__}: {e}{body}")
            return None

    def find_child_databases(self, parent_page_id: str,
                             title: str) -> list[dict]:
        """부모 페이지 바로 아래에서 제목이 같은 DB를 전부 찾는다.

        반환: [{"database_id", "data_source_id", "title"}] — 생성 순서(페이지 순서).
        제목이 같은 DB가 여럿이면 전부 돌려준다. 호출측이 중복을 알려야 한다.
        """
        found: list[dict] = []
        cursor: Optional[str] = None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            res = self._get(f"/blocks/{parent_page_id}/children", params)
            if not res:
                break
            for b in res.get("results", []):
                if b.get("type") != "child_database":
                    continue
                t = (b.get("child_database") or {}).get("title", "")
                if t.strip() != title.strip():
                    continue
                db_id = b.get("id")
                # 2025-09-03 부터 행을 넣는 ID는 data_source_id 다
                db = self._get(f"/databases/{db_id}") or {}
                ds = (db.get("data_sources") or [{}])[0].get("id") or db_id
                found.append({"database_id": db_id, "data_source_id": ds,
                              "title": t})
            if not res.get("has_more"):
                break
            cursor = res.get("next_cursor")
        return found

    def get_or_create_database(self, parent_page_id: str, schema: dict,
                               prefer_id: Optional[str] = None
                               ) -> tuple[Optional[str], str]:
        """있으면 재사용, 없으면 생성 → (데이터 소스 ID, "found"|"created"|"failed").

        왜 필요한가: --setup-db 를 다시 돌리면 같은 제목의 DB가 하나 더 생겼다.
        실측으로 '진입 신호 도달' DB가 3개 만들어졌다. 셋 중 하나만 .env 에
        있을 수 있고, 나머지는 영원히 빈 채로 아카이브를 어지럽힌다.

        중복이 있을 때 어느 것을 쓰나:
          1. prefer_id(.env 에 이미 적힌 ID)가 그중 하나면 **그것**. 이미 쓰고
             있는 DB를 두고 다른 걸 쓰라고 하면 .env 수정 + 데몬 재시작을
             시키는 셈이라, 정반대 안내가 된다(실측으로 그렇게 안내했었다).
          2. 아니면 가장 먼저 만든 것.
        나머지는 [중복] 으로 전부 출력해 사용자가 지울 수 있게 한다.
        """
        title = "".join(t.get("text", {}).get("content", "")
                        for t in schema.get("title", []))
        existing = self.find_child_databases(parent_page_id, title)
        if existing:
            pick = 0
            if prefer_id:
                for i, d in enumerate(existing):
                    if d["data_source_id"] == prefer_id.strip():
                        pick = i
                        break
            if len(existing) > 1:
                why = (".env 에 이미 설정된 것" if prefer_id and
                       existing[pick]["data_source_id"] == prefer_id.strip()
                       else "가장 먼저 만든 것")
                print(f"[notion] ⚠️ 제목이 같은 DB가 {len(existing)}개 있습니다 "
                      f"— {why}을 씁니다. 나머지는 노션에서 지우세요:")
                for i, d in enumerate(existing):
                    mark = "사용" if i == pick else "중복"
                    print(f"           [{mark}] data_source_id={d['data_source_id']}  "
                          f"(database_id={d['database_id']})")
            return existing[pick]["data_source_id"], "found"
        ds = self.create_database(parent_page_id, schema)
        return (ds, "created") if ds else (None, "failed")

    def create_database(self, parent_page_id: str, schema: dict) -> Optional[str]:
        """
        스키마 dict({"title":…, "properties":…})로 DB 생성 → **데이터 소스 ID** 반환.

        Notion API 2025-09-03 부터 DB 아래에 데이터 소스 계층이 생겨,
        행을 넣을 때 쓰는 ID는 database_id 가 아니라 data_source_id 다.
        """
        payload = {"parent": {"type": "page_id", "page_id": parent_page_id},
                   "title": schema["title"],
                   "initial_data_source": {"properties": schema["properties"]}}
        res = self._post("/databases", payload)
        if not res:
            return None
        return (res.get("data_sources") or [{}])[0].get("id") or res.get("id")

    def create_recommendations_db(self, parent_page_id: str,
                                  title: str = "오늘의 추천") -> Optional[str]:
        """1회 셋업: 부모 페이지 아래 추천 DB — 있으면 재사용 → 데이터 소스 ID."""
        ds, _ = self.get_or_create_database(parent_page_id,
                                            recommendations_db_schema(title))
        return ds

    def create_triggers_db(self, parent_page_id: str,
                           title: str = "진입 신호 도달") -> Optional[str]:
        """1회 셋업: 진입 트리거 도달 DB — 있으면 재사용 → 데이터 소스 ID."""
        ds, _ = self.ensure_triggers_db(parent_page_id, title)
        return ds

    def ensure_triggers_db(self, parent_page_id: str,
                           title: str = "진입 신호 도달",
                           prefer_id: Optional[str] = None) -> tuple[Optional[str], str]:
        """(데이터 소스 ID, "found"|"created"|"failed") — 호출측이 구분해 안내한다.
        prefer_id: .env 에 이미 적힌 ID. 중복 중에 그게 있으면 그걸 쓴다."""
        from stock_auto.tracking.trigger_notify import triggers_db_schema
        return self.get_or_create_database(parent_page_id, triggers_db_schema(title),
                                           prefer_id=prefer_id)

    def add_row(self, db_id: str, properties: dict,
                children: Optional[list] = None) -> Optional[str]:
        """임의 DB에 행 1개 추가 → 페이지 ID. properties는 호출측이 만든다."""
        payload: dict = {"parent": {"type": "data_source_id",
                                    "data_source_id": db_id},
                         "properties": properties}
        if children:
            payload["children"] = children[:100]
        res = self._post("/pages", payload)
        return res.get("id") if res else None

    def add_recommendation(self, db_id: str, reco: dict, market: str,
                           date: str, sector_label: str = "-",
                           row: Optional[dict] = None) -> Optional[str]:
        # row(screener 행)는 Effective·종목명·섹터상태의 정답 출처다 — recommendation_row 참조.
        # parent는 Notion API 2025-09-03 의 data_source_id 방식(VM에서 마이그레이션).
        props = recommendation_row(reco, market, date, sector_label, row)
        res = self._post("/pages", {"parent": {"type": "data_source_id",
                                               "data_source_id": db_id},
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
