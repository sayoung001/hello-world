"""
시계/시간대 헬퍼 — 시장별 '거래소 현지시각' 기준 날짜·시각.

핵심: SSH 서버의 로컬 시간대(UTC/KST 등)에 의존하지 말 것.
US 관련 날짜는 항상 ET(America/New_York) 기준으로 산출한다(naive now() 금지).
KR은 확장성용으로 유지.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from stock_auto.config.settings import Market

ET = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")


def market_tz(market: Market) -> ZoneInfo:
    return ET if market == Market.US else KST


def market_now(market: Market) -> datetime:
    """시장 거래소 현지시각(aware)."""
    return datetime.now(market_tz(market))


def market_today(market: Market) -> str:
    """시장 거래소 기준 오늘 날짜 'YYYY-MM-DD'."""
    return market_now(market).strftime("%Y-%m-%d")


def now_et() -> datetime:
    return datetime.now(ET)
