"""
섹터 장(場) 개요 — "지금 섹터 돈이 어디로 흐르나"를 한 화면에.

sector_status 는 게이트용 점수(0~5)와 라벨만 낸다. 추천이 0건인 날(매크로 L0)에도 사용자가
보고 싶은 것은 '그래도 섹터 장은 어떤가'다 — 어느 섹터가 오르고, 어디서 빠지고, 지수 대비
어디가 강한지, 전환 중인 섹터는 어디인지. 이 모듈은 섹터 ETF 일봉 하나로 그걸 다 계산해
텔레그램·Notion·PC 화면이 같은 줄을 쓴다.

행(섹터 ETF 1개) 항목:
  score/label          sector_status 와 동일한 진단(게이트가 쓰는 값)
  regime/transition/days_in_regime   레짐·전환 방향·연속일
  ret_1d/ret_5d/ret_20d  수익률 %
  rs_20d               지수(US500/KS11) 대비 20일 초과수익 %p — 로테이션의 핵심
  dist_52w             52주 고가 대비 %
  rvol                 상대 거래량
  n_stocks/pct_strong/pct_buy   (배치 뒤) 유니버스 종목 중 그 섹터의 정배열(R3+)·원신호 비율

사용:
  .venv/bin/python -m stock_auto.sector.sector_overview --market US
  .venv/bin/python -m stock_auto.sector.sector_overview --market KR --telegram   # 바로 폰으로
"""

from __future__ import annotations

import argparse
from datetime import timedelta
from typing import Any, Optional

import pandas as pd

from stock_auto.config.settings import Market, get_config
from stock_auto.config.clock import market_now
from stock_auto.data.downloader import load_or_download
from stock_auto.indicators.indicators_v2 import calculate_indicators
from stock_auto.strategy.regime_v2 import add_market_regime, detect_regime_transition
from stock_auto.strategy.strategies import apply_all_strategies
from stock_auto.sector.sector_analysis import diagnose_sector_status, US_SECTOR_TICKERS, KR_SECTOR_TICKERS

ICON = {5: "🟢", 4: "🟢", 3: "🟡", 2: "⚪", 1: "🟠", 0: "🔴"}
TRANS = {"improving": "↑개선", "deteriorating": "↓악화", "stable": "→유지"}


def _tickers(market: Market) -> dict[str, dict]:
    return US_SECTOR_TICKERS if market == Market.US else KR_SECTOR_TICKERS


def short_name(market: Market, etf: str, raw: str) -> str:
    """'기술 (Technology)' → '기술', 'KODEX 반도체' → '반도체'."""
    nm = (raw or etf).strip()
    if market == Market.US:
        return nm.split(" (")[0].strip() or etf
    for brand in ("KODEX", "TIGER", "KBSTAR", "RISE", "ACE", "SOL", "PLUS", "HANARO", "KOSEF", "ARIRANG"):
        if nm.startswith(brand + " "):
            nm = nm[len(brand) + 1:]
    return nm or etf


def _ret(close: pd.Series, n: int) -> Optional[float]:
    if len(close) <= n:
        return None
    a, b = float(close.iloc[-1 - n]), float(close.iloc[-1])
    return (b / a - 1) * 100 if a > 0 else None


def _load(symbols: list[str], market: Market, lookback_days: int) -> dict[str, pd.DataFrame]:
    start = (market_now(market) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    out: dict[str, pd.DataFrame] = {}
    for s in symbols:
        try:
            out[s] = load_or_download(s, start, None, market)
        except Exception as e:  # noqa: BLE001
            print(f"[sector] {s} 일봉 실패: {type(e).__name__}: {e}")
    return out


def compute_overview(market: Market, lookback_days: int = 400,
                     ohlcv_map: Optional[dict[str, pd.DataFrame]] = None,
                     index_df: Optional[pd.DataFrame] = None) -> list[dict[str, Any]]:
    """섹터 ETF 별 개요 행. ohlcv_map/index_df 주입 시 다운로드 생략(테스트·배치 재사용)."""
    tickers = _tickers(market)
    cfg = get_config(market)
    if ohlcv_map is None:
        ohlcv_map = _load(list(tickers), market, lookback_days)
    if index_df is None and cfg.index_symbols:
        index_df = _load([cfg.index_symbols[0]], market, lookback_days).get(cfg.index_symbols[0])
    idx_ret20 = _ret(index_df["Close"], 20) if index_df is not None and len(index_df) > 21 else None

    rows: list[dict[str, Any]] = []
    for etf, meta in tickers.items():
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
            last = ind.iloc[-1]
            label, _good, score = diagnose_sector_status(ind, last, rinfo)
            close = ind["Close"]
            r20 = _ret(close, 20)
            high52 = float(close.tail(252).max())
            rows.append({
                "etf": etf, "name": short_name(market, etf, meta.get("name", etf)),
                "full_name": meta.get("name", etf), "score": int(score), "label": str(label),
                "regime": int(rinfo["current"]), "transition": str(rinfo["transition"]),
                "days_in_regime": int(rinfo.get("days_in_regime", 0)),
                "ret_1d": _ret(close, 1), "ret_5d": _ret(close, 5), "ret_20d": r20,
                "rs_20d": (r20 - idx_ret20) if (r20 is not None and idx_ret20 is not None) else None,
                "dist_52w": (float(close.iloc[-1]) / high52 - 1) * 100 if high52 > 0 else None,
                "rvol": float(last.get("rvol", 0) or 0),
                "above_ma20": bool(float(close.iloc[-1]) > float(last.get("MA20", 0) or 0)),
                "bar_date": pd.Timestamp(ind.index[-1]).strftime("%Y-%m-%d"),
                "n_stocks": None, "pct_strong": None, "pct_buy": None,
            })
        except Exception as e:  # noqa: BLE001 — 한 섹터 실패가 전체를 막지 않게
            print(f"[sector] {etf} 진단 실패: {type(e).__name__}: {e}")
    rows.sort(key=lambda r: (-r["score"], -(r["rs_20d"] if r["rs_20d"] is not None else -999)))
    return rows


def attach_breadth(rows: list[dict], scored_all: Optional[pd.DataFrame]) -> list[dict]:
    """배치 채점표(scored_all: symbol·sector_etf·stock_regime·raw_buy)로 섹터별 종목 폭을 붙인다."""
    if scored_all is None or scored_all.empty or "sector_etf" not in scored_all.columns:
        return rows
    g = scored_all.groupby("sector_etf")
    for r in rows:
        if r["etf"] not in g.groups:
            continue
        sub = g.get_group(r["etf"])
        n = len(sub)
        r["n_stocks"] = int(n)
        if "stock_regime" in sub.columns and n:
            r["pct_strong"] = float((pd.to_numeric(sub["stock_regime"], errors="coerce") >= 3).mean() * 100)
        if "raw_buy" in sub.columns and n:
            r["pct_buy"] = float(sub["raw_buy"].astype(bool).mean() * 100)
    return rows


def scores_labels(rows: list[dict]) -> tuple[dict[str, int], dict[str, str]]:
    """screener 게이트 입력(sector_status) — compute_sector_status 와 같은 모양."""
    return {r["etf"]: r["score"] for r in rows}, {r["etf"]: r["label"] for r in rows}


def _pct(v: Optional[float], sign: bool = True) -> str:
    if v is None:
        return "-"
    return f"{v:+.1f}%" if sign else f"{v:.0f}%"


def overview_lines(rows: list[dict], with_rotation: bool = True) -> list[str]:
    """텔레그램·Notion·PC 화면 공용 줄. 점수 내림차순, 한 섹터 한 줄."""
    L: list[str] = []
    for r in rows:
        core = (f"{ICON.get(r['score'], '⚪')} {r['name']} ({r['etf']}) {r['label']}[{r['score']}] · "
                f"1D {_pct(r['ret_1d'])} 5D {_pct(r['ret_5d'])} 20D {_pct(r['ret_20d'])}")
        if r.get("rs_20d") is not None:
            core += f" · 지수대비 {_pct(r['rs_20d'])}"
        core += f" · {TRANS.get(r['transition'], r['transition'])} {r['days_in_regime']}일"
        if r.get("n_stocks"):
            core += f" · 정배열 {_pct(r['pct_strong'], sign=False)}/{r['n_stocks']}종목"
        L.append(core)
    if with_rotation and rows:
        L += rotation_lines(rows)
    return L


def rotation_lines(rows: list[dict]) -> list[str]:
    """🧭 로테이션 요약 — 지수 대비 강한 곳/약한 곳/전환 중인 곳."""
    rs = [r for r in rows if r.get("rs_20d") is not None]
    L: list[str] = []
    if rs:
        rs_sorted = sorted(rs, key=lambda r: -r["rs_20d"])
        top = [f"{r['name']} {_pct(r['rs_20d'])}" for r in rs_sorted[:3]]
        bot = [f"{r['name']} {_pct(r['rs_20d'])}" for r in rs_sorted[-3:][::-1]]
        L.append("🧭 지수 대비 20일 강세: " + " · ".join(top))
        L.append("🧭 지수 대비 20일 약세: " + " · ".join(bot))
    imp = [r["name"] for r in rows if r["transition"] == "improving" and r["score"] <= 3]
    det = [r["name"] for r in rows if r["transition"] == "deteriorating" and r["score"] >= 3]
    if imp:
        L.append("🧭 바닥에서 개선 중: " + " · ".join(imp[:5]))
    if det:
        L.append("🧭 강세에서 악화 중: " + " · ".join(det[:5]))
    n_up = sum(1 for r in rows if r["score"] >= 3)
    L.append(f"🧭 섹터 폭: {n_up}/{len(rows)} 섹터가 회복 이상(≥3)"
             + (" — 지수 약세와 어긋남(종목장)" if rows and n_up >= len(rows) * 0.6 else ""))
    return L


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="섹터 장 개요")
    ap.add_argument("--market", default="US")
    ap.add_argument("--telegram", action="store_true", help="그 시장 채널로 전송")
    a = ap.parse_args(argv)
    from stock_auto.config.env import load_env, get_secrets
    load_env()
    market = Market(a.market.upper())
    rows = compute_overview(market)
    lines = overview_lines(rows)
    text = f"🏭 [{market.value}] 섹터 장 개요 ({rows[0]['bar_date'] if rows else '-'} 기준)\n" + "\n".join(lines)
    print(text)
    if a.telegram:
        from stock_auto.notify.telegram import for_market
        tg = for_market(market, get_secrets())
        ok = bool(getattr(tg, "enabled", False)) and tg.send_message(text, parse_mode="")
        print("텔레그램 " + ("전송" if ok else "미설정/실패"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
