"""
실시간 감시 종목 선정 — KIS 실시간 등록 건수 제한 대응.

문제:
  유니버스를 지수 구성종목(약 500)으로 키우면 일봉 스캔은 문제가 없지만,
  **실시간 모니터는 그 500개를 다 볼 수 없다.** KIS Open API는 접속(approval_key)당
  실시간 등록 건수가 제한되어 있다(정규 40건 내외). 제한을 넘긴 등록 요청은
  거부되는데, 현재 피드는 응답을 확인하지 않으므로 **조용히 빠진다.**

선정 원칙(우선순위):
  1. 보유 종목 — 청산 판단이 필요하므로 무조건 포함
  2. 직전 배치의 매수 후보 — 오늘 살지도 모르는 종목
  3. 남는 자리에 유니버스 상위(직전 배치 Effective 순)

즉 "온 시장을 감시"가 아니라 **"오늘 관심 있는 종목만 감시"** 로 방향을 바꾼다.
어차피 폭주 알림의 목적은 추천 후보의 진입 타이밍 보조다(사용자 정책: 추천 위주).
"""

from __future__ import annotations

import os
from typing import Optional

from stock_auto.config.settings import Market

# KIS 실시간 등록 건수 제한. 계정/상품에 따라 다르므로 환경변수로 조정 가능.
DEFAULT_MAX = int(os.environ.get("KIS_MAX_SUBSCRIPTIONS", "40"))


def build(market: Market, universe: list[str],
          limit: int = DEFAULT_MAX,
          signals_path: Optional[str] = None) -> list[str]:
    """감시 대상 종목 리스트(<= limit)를 만든다."""
    picked: list[str] = []

    def _add(syms) -> None:
        for s in syms:
            if s and s not in picked and len(picked) < limit:
                picked.append(s)

    _add(_held(market))
    _add(_recent_candidates(market, signals_path))
    _add(universe)

    if len(universe) > limit:
        print(f"[watchlist] 유니버스 {len(universe)}종목 중 {len(picked)}종목만 실시간 감시 "
              f"(KIS 등록 한도 {limit}). 보유·직전 후보 우선.")
    return picked


def _held(market: Market) -> list[str]:
    """보유 종목 — positions.csv가 있으면 읽는다(없으면 빈 리스트)."""
    try:
        from stock_auto.tracking import positions
        return [p["symbol"] for p in positions.open_positions()
                if p.get("market", "US") == market.value]
    except Exception:  # noqa: BLE001 — 보유 파일이 아직 없을 수 있다
        return []


def _recent_candidates(market: Market, signals_path: Optional[str] = None,
                       days: int = 5) -> list[str]:
    """최근 며칠간의 배치 추천 종목 — Effective 높은 순."""
    try:
        from stock_auto.tracking import store
        rows = store.load(signals_path or store.DEFAULT_PATH)
    except Exception:  # noqa: BLE001
        return []
    recs = [r for r in rows
            if r.get("source") == "batch" and r.get("market") == market.value
            and not r.get("gated_by")]
    if not recs:
        return []
    dates = sorted({r["date"] for r in recs}, reverse=True)[:days]
    recent = [r for r in recs if r["date"] in dates]

    def _eff(r) -> float:
        try:
            return float(r.get("effective_score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    recent.sort(key=_eff, reverse=True)
    out: list[str] = []
    for r in recent:
        if r["symbol"] not in out:
            out.append(r["symbol"])
    return out
