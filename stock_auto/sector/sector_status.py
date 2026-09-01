"""
섹터 상태 산출 (경량) — 일일 배치가 섹터 게이트를 쓸 수 있게 한다.

기존 sector_analysis.analyze_sectors()는 차트 저장·CSV 출력·텔레그램 전송까지
함께 수행하는 '단독 실행용' 함수다. 배치는 상태 점수만 필요하므로
같은 진단 로직(diagnose_sector_status)을 재사용하되 부수효과 없이 산출한다.

반환:
  scores : {섹터ETF: 0~5}      → screener 의 sector_status 인자 (매수 게이트)
  labels : {섹터ETF: '강력매수'...}
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

import pandas as pd

from stock_auto.config.settings import Market
from stock_auto.config.clock import market_now
from stock_auto.data.downloader import load_or_download
from stock_auto.indicators.indicators_v2 import calculate_indicators
from stock_auto.strategy.regime_v2 import add_market_regime, detect_regime_transition
from stock_auto.strategy.strategies import apply_all_strategies
from stock_auto.sector.sector_analysis import (
    diagnose_sector_status, US_SECTOR_TICKERS, KR_SECTOR_TICKERS,
)


def sector_tickers(market: Market) -> dict[str, dict]:
    return US_SECTOR_TICKERS if market == Market.US else KR_SECTOR_TICKERS


def compute_sector_status(
    market: Market,
    lookback_days: int = 400,
    ohlcv_map: Optional[dict[str, pd.DataFrame]] = None,
) -> tuple[dict[str, int], dict[str, str]]:
    """섹터 ETF별 상태 점수(0~5)와 라벨. ohlcv_map 주입 시 다운로드 생략(테스트)."""
    tickers = sector_tickers(market)
    start = (market_now(market) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    if ohlcv_map is None:
        ohlcv_map = {}
        for etf in tickers:
            try:
                ohlcv_map[etf] = load_or_download(etf, start, None, market)
            except Exception as e:  # noqa: BLE001
                print(f"[sector] {etf} 일봉 실패: {type(e).__name__}: {e}")

    scores: dict[str, int] = {}
    labels: dict[str, str] = {}
    for etf in tickers:
        df = ohlcv_map.get(etf)
        if df is None or len(df) < 60:
            continue
        try:
            ind = calculate_indicators(df.copy(), market=market.value)
            if ind is None or len(ind) < 60:
                continue
            ind = add_market_regime(ind)
            rinfo = detect_regime_transition(ind)
            ind = apply_all_strategies(ind, regime=rinfo["current"])
            label, _is_good, score = diagnose_sector_status(
                ind, ind.iloc[-1], rinfo)
            scores[etf] = int(score)
            labels[etf] = str(label)
        except Exception as e:  # noqa: BLE001 — 한 섹터 실패가 전체를 막지 않게
            print(f"[sector] {etf} 진단 실패: {type(e).__name__}: {e}")
    return scores, labels


def labels_for_symbols(stock_sector_etf: dict[str, str],
                       sector_labels: dict[str, str]) -> dict[str, str]:
    """{종목: 섹터ETF} + {섹터ETF: 라벨} → {종목: 라벨} (LLM 프롬프트용)."""
    return {sym: sector_labels.get(etf, "-")
            for sym, etf in (stock_sector_etf or {}).items()}


def summary_lines(scores: dict[str, int], labels: dict[str, str],
                  market: Market) -> list[str]:
    """Notion/콘솔 출력용 요약 줄. 점수 내림차순."""
    names = sector_tickers(market)
    out = []
    for etf, sc in sorted(scores.items(), key=lambda kv: -kv[1]):
        nm = names.get(etf, {}).get("name", etf)
        out.append(f"{nm} ({etf}) — {labels.get(etf, '-')} [{sc}]")
    return out
