"""
트리거 발화 — 진입 조건에 도달했을 때 Notion에 정리하고 Telegram으로 알린다.

사용자 요구:
  "1.5% 눌림 시 분할 진입" → 기록 → 1.5% 눌리면
   → Notion에 정리(종목코드 / 추천 근거 / 카테고리(섹터) / 차트 / 단기·중기·장기)
   → Telegram에 "정리하였다" 단축 알림 + 링크

Notion 페이지는 '진입 신호 도달' DB에 1행으로 쌓는다. 일일 추천 DB와 분리하는 이유:
  추천은 '하루에 N건 제안', 도달은 '조건이 맞은 순간'이다. 수명과 조회 방식이 달라
  같은 DB에 섞으면 둘 다 보기 어려워진다.

차트는 Notion 파일 업로드가 번거로워 **Telegram으로 보내고** Notion에는 차트에서
읽히는 수치(종가·EMA·RSI·거래량비)를 텍스트로 남긴다. 이미지는 폰에서 보고
수치는 나중에 검색한다 — 쓰임이 갈린다.
"""

from __future__ import annotations

from typing import Any, Optional

# ── Notion: '진입 신호 도달' DB ────────────────────────────────────────────
HORIZON_BUCKET = {"단타": "단기", "중단기": "중기", "스윙": "장기"}

_TRUE = {"yes", "y", "true", "1", "t", "on"}


def _is_split(t: dict) -> bool:
    """분할 진입 여부. 생성부는 "yes"를 넣지만 CSV를 거치면 bool이 "True"가 되고,
    Trigger를 직접 만들면 bool이 그대로 들어온다. 어느 쪽이든 참으로 읽어야
    '분할 진입 권고'가 조용히 사라지지 않는다."""
    v = t.get("split", "")
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in _TRUE


def triggers_db_schema(title: str = "진입 신호 도달") -> dict:
    """사용자가 요청한 항목으로 구성한 DB 스키마."""
    return {
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": {
            "종목": {"title": {}},
            "시장": {"select": {"options": [
                {"name": "US", "color": "blue"}, {"name": "KR", "color": "red"}]}},
            "카테고리": {"select": {"options": [
                {"name": n, "color": c} for n, c in (
                    ("기술", "blue"), ("반도체", "purple"), ("커뮤니케이션", "pink"),
                    ("임의소비재", "orange"), ("필수소비재", "brown"),
                    ("헬스케어", "green"), ("금융", "yellow"), ("산업재", "gray"),
                    ("에너지", "red"), ("소재", "default"),
                    ("유틸리티", "blue"), ("부동산", "brown"), ("미상", "gray"))]}},
            "기간": {"select": {"options": [
                {"name": "단기", "color": "red"},
                {"name": "중기", "color": "yellow"},
                {"name": "장기", "color": "green"}]}},
            "진입조건": {"rich_text": {}},
            "도달가": {"number": {"format": "number"}},
            "기준가": {"number": {"format": "number"}},
            "손절": {"number": {"format": "number"}},
            "목표": {"number": {"format": "number"}},
            "분할진입": {"checkbox": {}},
            "Effective": {"number": {"format": "number"}},
            "확신도": {"number": {"format": "percent"}},
            "섹터상태": {"rich_text": {}},
            "추천근거": {"rich_text": {}},
            "도달시각": {"date": {}},
        },
    }


def _rt(text: str) -> list:
    return [{"type": "text", "text": {"content": str(text)[:1900]}}]


def _num(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


# 섹터 ETF → 사람이 읽는 카테고리
ETF_CATEGORY = {
    "XLK": "기술", "SOXX": "반도체", "XLC": "커뮤니케이션", "XLY": "임의소비재",
    "XLP": "필수소비재", "XLV": "헬스케어", "XLF": "금융", "XLI": "산업재",
    "XLE": "에너지", "XLB": "소재", "XLU": "유틸리티", "XLRE": "부동산",
}


def category_of(trigger: dict) -> str:
    return ETF_CATEGORY.get(str(trigger.get("sector_etf", "")).upper(), "미상")


def iso_when(fired_at: str) -> str:
    """Notion 날짜 속성은 ISO 8601 만 받는다.
    일봉 경로는 'YYYY-MM-DD', 실시간 경로는 ISO(오프셋 포함)를 주지만, 예전
    실시간 경로가 'YYYY-MM-DD HH:MM'(공백)을 줘서 Notion 이 400 으로 거부했고
    그 실패는 삼켜져 알림에 링크만 빠졌다. 어떤 형식이 와도 ISO 로 맞춘다."""
    s = str(fired_at or "").strip()
    if len(s) >= 16 and s[10] == " ":
        s = s[:10] + "T" + s[11:]
    if len(s) == 16:          # 'YYYY-MM-DDTHH:MM' → 초 보충
        s += ":00"
    return s


def trigger_row(trigger: dict, fired_price: float, fired_at: str,
                chart_note: str = "") -> dict:
    """Trigger(dict) → Notion 행 properties."""
    rationale = str(trigger.get("rationale", ""))
    if chart_note:
        rationale = (rationale + "\n[차트] " + chart_note).strip()
    return {
        "종목": {"title": _rt(trigger.get("symbol", "-"))},
        "시장": {"select": {"name": trigger.get("market") or "US"}},
        "카테고리": {"select": {"name": category_of(trigger)}},
        "기간": {"select": {"name": HORIZON_BUCKET.get(
            str(trigger.get("horizon", "")), "중기")}},
        "진입조건": {"rich_text": _rt(_condition_text(trigger))},
        "도달가": {"number": _num(fired_price)},
        "기준가": {"number": _num(trigger.get("basis_price"))},
        "손절": {"number": _num(trigger.get("stop"))},
        "목표": {"number": _num(trigger.get("target"))},
        "분할진입": {"checkbox": _is_split(trigger)},
        "Effective": {"number": _num(trigger.get("effective_score"))},
        "확신도": {"number": _num(trigger.get("conviction"))},
        "섹터상태": {"rich_text": _rt(trigger.get("sector_label") or "-")},
        "추천근거": {"rich_text": _rt(rationale or "-")},
        "도달시각": {"date": {"start": iso_when(fired_at)}},
    }


def _condition_text(t: dict) -> str:
    kind = str(t.get("kind", ""))
    v = t.get("value_pct")
    basis = {"signal_close": "추천일 종가", "prev_close": "전일 종가",
             "open": "당일 시가"}.get(str(t.get("basis", "")), "기준가")
    label = {"pullback": "눌림", "breakout": "돌파", "immediate": "즉시"}.get(kind, kind)
    head = f"{basis} 대비 {v}% {label}" if kind != "immediate" else "조건 없음(즉시)"
    if _is_split(t):
        head += " · 분할 진입"
    raw = str(t.get("rule_text", "")).strip()
    return f"{head}" + (f"  ({raw})" if raw else "")


def trigger_page_blocks(trigger: dict, fired_price: float,
                        chart_note: str = "") -> list:
    """도달 페이지 본문 — 근거를 사람이 읽는 순서로."""
    from stock_auto.integrations.notion_publisher import (
        _heading, _bullet, _callout)
    b = [_heading(f"{trigger.get('symbol')} — {_condition_text(trigger)} 도달", 2)]
    b.append(_heading("가격", 3))
    for line in (f"기준가 {trigger.get('basis_price')}",
                 f"발화 목표 {trigger.get('target_price')}",
                 f"실제 도달가 {fired_price}",
                 f"손절 {trigger.get('stop')} · 목표 {trigger.get('target')}"):
        b.append(_bullet(line))

    b.append(_heading("분류", 3))
    b.append(_bullet(f"카테고리: {category_of(trigger)} "
                     f"({trigger.get('sector_etf') or '-'})"))
    b.append(_bullet(f"섹터 상태: {trigger.get('sector_label') or '-'}"))
    b.append(_bullet(f"기간: {HORIZON_BUCKET.get(str(trigger.get('horizon','')),'중기')} "
                     f"({trigger.get('horizon') or '-'})"))

    b.append(_heading("추천 근거", 3))
    rat = str(trigger.get("rationale", "")).strip()
    if rat:
        for part in rat.split(" / "):
            if part.strip():
                b.append(_bullet(part.strip()))
    else:
        b.append(_bullet("근거 기록 없음"))
    b.append(_bullet(f"규칙 신호: {trigger.get('strategies') or '-'} "
                     f"· Effective {trigger.get('effective_score') or '-'}"))

    if chart_note:
        b.append(_heading("차트 수치", 3))
        for line in chart_note.split(" · "):
            if line.strip():
                b.append(_bullet(line.strip()))
        b.append(_bullet("차트 이미지는 텔레그램으로 전송됨"))

    b.append(_callout("진입 조건 도달 기록입니다. 분석 보조이며 투자 권유가 아닙니다. "
                      "실제 매수 여부와 수량은 본인이 판단하세요."))
    return b


# ── Telegram 단축 알림 ────────────────────────────────────────────────────
def short_alert(trigger: dict, fired_price: float, notion_url: str = "") -> str:
    """
    짧게. 폰 알림 한 줄에 들어가야 쓸모가 있다.
    세부는 Notion 링크로 넘긴다.
    """
    sym = trigger.get("symbol", "-")
    cond = _condition_text(trigger).split("  (")[0]
    bucket = HORIZON_BUCKET.get(str(trigger.get("horizon", "")), "중기")
    cat = category_of(trigger)
    lines = [
        f"🎯 {sym} 진입 조건 도달",
        f"{cond} · {fired_price:,.2f}",
        f"{cat} · {bucket} · 손절 {trigger.get('stop') or '-'}",
    ]
    if _is_split(trigger):
        lines.append("※ 분할 진입 권고")
    lines.append("노션에 정리했습니다" + (f" → {notion_url}" if notion_url else ""))
    return "\n".join(lines)


def chart_note_from(df) -> str:
    """차트에서 읽히는 수치를 한 줄로 — Notion에 텍스트로 남긴다."""
    try:
        last = df.iloc[-1]
        parts = [f"종가 {float(last['Close']):,.2f}"]
        for col, label in (("EMA20", "EMA20"), ("EMA60", "EMA60"),
                           ("rsi_14", "RSI"), ("rvol", "RVOL"),
                           ("atr", "ATR")):
            if col in df.columns:
                v = last.get(col)
                if v is not None and v == v:
                    parts.append(f"{label} {float(v):,.2f}")
        if "Volume" in df.columns and len(df) >= 20:
            vr = float(last["Volume"]) / float(df["Volume"].tail(20).mean())
            parts.append(f"거래량 20일평균비 {vr:.2f}배")
        return " · ".join(parts)
    except Exception:  # noqa: BLE001 — 수치 산출 실패가 알림을 막지 않게
        return ""
