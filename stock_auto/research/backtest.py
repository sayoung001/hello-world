"""
1년 백테스트 — 규칙엔진 신호를 과거 1년에 전부 발생시켜 결과를 붙이고,
"이긴 신호는 무엇이 달랐나(손실과 비교한 특이점)" 를 attribution 으로 찍는다.

운영 추천 기록(signals.csv)은 두 달치 130건이라 특이점을 통계로 말하기엔 얇다. 같은
규칙엔진(indicators_v2 → regime_v2 → strategies → 삼중 배리어 라벨러)을 과거 1년에
그대로 돌리면 수천 건이 나온다. LLM 단계(확신도·진입 조건)는 과거에 없으니 빼고,
**규칙엔진 신호 + 그날의 시장 맥락** 으로만 비교한다.

look-ahead 방지:
  · 신호일 t 의 피처는 t 일 봉까지로만 계산(지표는 롤링이라 t 행 값이 그 시점 값)
  · 횡단면 모멘텀 순위도 그날 유니버스 안에서만
  · 진입은 t+1 시가(labeler 와 동일), 결과는 t+1 이후 봉으로만
  · 유니버스는 '오늘'의 지수 구성종목이다 — 생존 편향이 있다(1년 안에 편입·퇴출된 종목은
    빠짐). 결과 문구에 명시한다.

운영과의 차이(의도): 운영은 Final_Buy 상위 15개(top_n)만 추천하고 섹터·실적 게이트가
있다. 여기서는 --top-n 으로 그날 순위 상위 N 만 쓰거나(기본 15, 운영 재현) 0 으로 전부
본다. 섹터 상태는 섹터 ETF 의 **그날 레짐** 으로 대신한다. 실적 게이트는 과거 일정이
없어 생략한다(그래서 실적 갭이 섞인다 — 손실 쪽 특이점에 'ATR/종가 큼'이 뜨면 그 영향).

수익 정의(사용자 기준): +2% 이상 = 수익, 0% 미만 = 손실, 0~2% = 미미(비교 제외).
특이점 수용: 수익 쪽은 구간 평균 수익률 ≥ 2% 인 것만.

사용:
  .venv/bin/python -m stock_auto.research.backtest                      # US 1년, 콘솔
  .venv/bin/python -m stock_auto.research.backtest --market KR --days 365
  .venv/bin/python -m stock_auto.research.backtest --top-n 0            # 그날 순위 무시, 전 신호
  .venv/bin/python -m stock_auto.research.backtest --notion --telegram  # 게시·전송
데몬은 일요일 11:00 KST 와, 보고서가 한 번도 없을 때 시작 직후에 별 프로세스로 돈다.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from stock_auto.config.settings import Market, get_config, TOP_N_FOR_AGENTS
from stock_auto.tracking import attribution as A

WIN_PCT = 2.0            # 사용자 기준: 수익 = +2% 이상
ACCEPT_PCT = 2.0         # 수익 쪽 특이점 수용: 구간 평균 ≥ 2%
REPORT_VERSION = 2       # 보고서 형식이 바뀌면 올린다 → 데몬이 시작 때 한 번 다시 돈다(.done 에 기록)
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
FWD_HORIZONS = (1, 3, 5, 10, 20)

# attribution 에 넘길 피처 정의 — 운영 기록과 겹치는 것은 이름을 맞춘다
NUMERIC: list[tuple[str, str]] = [
    ("effective_score", "Effective 점수"), ("money_score", "자금 점수"), ("price_score", "가격 점수"),
    ("liquidity_score", "유동성 점수"), ("penalty", "감점"), ("warning_count", "경고 수"),
    ("rsi_14", "RSI(14)"), ("adx", "ADX"), ("rvol", "상대 거래량(RVOL)"),
    ("mom_rank", "모멘텀 순위(0~1)"), ("mom_12_1", "12-1 모멘텀"), ("atr_pct", "ATR/종가 %"),
    ("stop_pct", "손절 폭 %"), ("target_pct", "목표 폭 %"), ("rr", "손익비(목표/손절)"),
    ("ret_5d", "직전 5일 수익 %"), ("ret_20d", "직전 20일 수익 %"), ("dist_52w_high", "52주 고가 대비 %"),
    ("ema20_gap", "EMA20 대비 %"), ("bb_pctb", "볼린저 %b"), ("vol_ratio20", "거래량/20일평균"),
    ("gap_entry", "진입 갭(익일 시가) %"), ("n_strats", "동시 발동 전략 수"),
    ("day_rank", "그날 Effective 순위"), ("day_breadth", "그날 신호 수"),
    ("macro_level", "매크로 레짐 수치"), ("sector_regime_num", "섹터 ETF 레짐 수치"),
]
CATEGORICAL: list[tuple[str, str]] = [
    ("horizon", "보유기간(전략 스타일)"), ("stock_regime", "종목 레짐"), ("regime_trans", "레짐 전환"),
    ("macro_regime", "매크로 레짐"), ("sector_regime", "섹터 ETF 레짐"), ("sector_etf", "섹터 ETF"),
    ("has_money", "자금 신호 있음"), ("weekday", "신호 요일"), ("month", "신호 월"),
    ("top15", "그날 상위 15 안"), ("gap_dir", "진입 갭 방향"),
]
# 전략 이름은 한 신호에 여러 개 — 각 전략을 '포함/미포함' 범주로 따로 둔다
STRATEGY_NAMES = ["VolBreakout", "VolPumpSustained", "MoneyFlowSurge", "MeanReversion", "GapMomentum",
                  "PullbackBuy", "MACDReversal", "SqueezeBreakout", "IchimokuTrend", "TrendContinuation"]
OUTCOME_SIDE: list[tuple[str, str]] = [
    ("mae_pct", "최대 역행(MAE) %"), ("mfe_pct", "최대 순행(MFE) %"), ("days_held", "보유일"),
]


def log(msg: str) -> None:
    print(f"[backtest] {msg}", flush=True)


def _quiet_pandas() -> None:
    """strategies.py 가 열을 하나씩 붙여 pandas 가 종목마다 '조각화' 경고를 수백 줄 낸다 — 로그만 묻힌다."""
    import warnings
    warnings.simplefilter("ignore", pd.errors.PerformanceWarning)
    warnings.simplefilter("ignore", FutureWarning)


# ── 1. 데이터 ──────────────────────────────────────────────────────────────
def load_universe(market: Market) -> dict[str, str]:
    from stock_auto.config.universe import resolve_universe
    return resolve_universe(market)


def load_prices(symbols: list[str], market: Market, start: str) -> dict[str, pd.DataFrame]:
    from stock_auto.data.downloader import load_or_download
    out: dict[str, pd.DataFrame] = {}
    t0 = time.time()
    for i, s in enumerate(symbols, 1):
        try:
            df = load_or_download(s, start, None, market)
            if df is not None and len(df) >= 120:
                out[s] = df
        except Exception as e:  # noqa: BLE001
            log(f"{s} 로드 실패: {type(e).__name__}: {str(e)[:80]}")
        if i % 50 == 0:
            log(f"가격 로드 {i}/{len(symbols)} ({time.time() - t0:.0f}s)")
    return out


# ── 2. 지표·신호 (종목당 1회, 전 구간 벡터) ─────────────────────────────────
def enrich(df: pd.DataFrame, market: Market) -> Optional[pd.DataFrame]:
    """운영 스크리너와 같은 파이프라인을 **전 구간**에 적용. 마지막 행만 쓰지 않는다."""
    from stock_auto.indicators.indicators_v2 import calculate_indicators
    from stock_auto.strategy.regime_v2 import add_market_regime
    from stock_auto.strategy.strategies import apply_all_strategies, check_exit_signals
    ind = calculate_indicators(df.copy(), market=market.value)
    if ind is None or len(ind) < 80:
        return None
    ind = add_market_regime(ind)
    ind = apply_all_strategies(ind, regime=None)       # 레짐 게이트는 아래서 행 단위로
    ind = check_exit_signals(ind)
    if "Regime" in ind.columns:
        ind["Final_Buy"] = ind["Final_Buy"] & (ind["Regime"] >= 1)
    return ind


def regime_series(df: pd.DataFrame, market: Market) -> Optional[pd.Series]:
    """지수·섹터 ETF 의 날짜별 레짐(0~4)."""
    from stock_auto.indicators.indicators_v2 import calculate_indicators
    from stock_auto.strategy.regime_v2 import add_market_regime
    ind = calculate_indicators(df.copy(), market=market.value)
    if ind is None or "Close" not in ind.columns:
        return None
    ind = add_market_regime(ind)
    return ind["Regime"].astype(int) if "Regime" in ind.columns else None


def macro_series(index_map: dict[str, pd.DataFrame], market: Market) -> pd.Series:
    """날짜별 매크로 레짐 — 운영과 같은 규칙(그룹 내 max → 그룹 간 min). 데이터 없으면 2."""
    cfg = get_config(market)
    per: dict[str, pd.Series] = {}
    for sym, df in index_map.items():
        r = regime_series(df, market)
        if r is not None:
            per[sym] = r
    if not per:
        return pd.Series(dtype=int)
    groups = cfg.index_groups or tuple((s,) for s in cfg.index_symbols)
    gser = []
    for g in groups:
        cols = [per[s] for s in g if s in per]
        if cols:
            gser.append(pd.concat(cols, axis=1).max(axis=1))
    out = pd.concat(gser, axis=1).min(axis=1).ffill()
    return out.astype(int)


def momentum_rank_table(enriched: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """날짜×종목 12-1 모멘텀과 그 횡단면 백분위(0~1). cross_sectional 과 같은 정의."""
    closes = pd.DataFrame({s: d["Close"] for s, d in enriched.items()}).sort_index()
    mom = closes.shift(21) / closes.shift(252) - 1.0
    short = closes.shift(21) / closes.shift(126) - 1.0
    mom = mom.where(mom.notna(), short)                 # 이력 짧으면 6-1 로 대체
    rank = mom.rank(axis=1, pct=True)
    return mom, rank


# ── 3. 신호 생성 + 결과 라벨 ───────────────────────────────────────────────
def _style_horizon(styles: set[str]) -> str:
    for s in ("단타", "중단기", "스윙"):
        if s in styles:
            return s
    return ""


def build_signals(enriched: dict[str, pd.DataFrame], market: Market, start_day: str,
                  sector_map: dict[str, str], macro: pd.Series,
                  sector_reg: dict[str, pd.Series], mom: pd.DataFrame, rank: pd.DataFrame,
                  top_n: int) -> list[dict[str, Any]]:
    from stock_auto.strategy.strategies import get_active_strategies, calculate_strategy_levels
    from stock_auto.tracking.labeler import label_record, HOLD_DAYS, DEFAULT_HOLD
    sigs: list[dict[str, Any]] = []
    for sym, df in enriched.items():
        buys = df[(df.index >= pd.Timestamp(start_day)) & df["Final_Buy"].fillna(False)]
        if buys.empty:
            continue
        closes = df["Close"]
        high52 = closes.rolling(252, min_periods=60).max()
        vol_ma20 = df["Volume"].rolling(20).mean()
        for ts, row in buys.iterrows():
            active = get_active_strategies(row)
            if not active:
                continue
            levels = calculate_strategy_levels(row, active)
            close = float(row["Close"])
            if not close or levels.get("stop") is None:
                continue
            styles = {a["style"] for a in active}
            horizon = _style_horizon(styles)
            pos = df.index.get_loc(ts)
            rec = {"date": ts.strftime("%Y-%m-%d"), "target": levels["target"], "stop": levels["stop"],
                   "atr": float(row.get("atr", 0) or 0), "horizon": horizon}
            out = label_record(rec, df, hold_days=HOLD_DAYS.get(horizon, DEFAULT_HOLD))
            if out.exit_type not in ("tp", "sl", "timeout"):
                continue                                   # 미체결(갭)·결과 미도달은 제외
            nxt_open = float(df.iloc[pos + 1]["Open"]) if pos + 1 < len(df) else None
            atr = rec["atr"]
            sec_etf = sector_map.get(sym, "-")
            sreg = sector_reg.get(sec_etf)
            sreg_v = int(sreg.get(ts)) if (sreg is not None and ts in sreg.index and pd.notna(sreg.get(ts))) else None
            mlev = int(macro.get(ts)) if (len(macro) and ts in macro.index) else None
            mr = rank[sym].get(ts) if sym in rank.columns else None
            mv = mom[sym].get(ts) if sym in mom.columns else None
            s = {
                "symbol": sym, "date": rec["date"], "market": market.value, "ts": ts,
                "effective_score": float(row.get("Effective_Score", 0)), "money_score": float(row.get("Money_Score", 0)),
                "price_score": float(row.get("Price_Score", 0)), "liquidity_score": float(row.get("liquidity_score", 0)),
                "penalty": float(row.get("Penalty", 0)), "warning_count": float(row.get("Warning_Count", 0)),
                "rsi_14": float(row.get("rsi_14", 0)), "adx": float(row.get("adx", 0)), "rvol": float(row.get("rvol", 0)),
                "mom_rank": None if mr is None or pd.isna(mr) else float(mr),
                "mom_12_1": None if mv is None or pd.isna(mv) else float(mv) * 100,
                "atr_pct": (atr / close * 100) if atr else None,
                "stop_pct": levels.get("stop_pct"), "target_pct": levels.get("target_pct"), "rr": levels.get("rr_ratio"),
                "ret_5d": (close / float(closes.iloc[pos - 5]) - 1) * 100 if pos >= 5 else None,
                "ret_20d": (close / float(closes.iloc[pos - 20]) - 1) * 100 if pos >= 20 else None,
                "dist_52w_high": (close / float(high52.iloc[pos]) - 1) * 100 if pd.notna(high52.iloc[pos]) else None,
                "ema20_gap": (close / float(row["EMA20"]) - 1) * 100 if row.get("EMA20") else None,
                "bb_pctb": float(row["bb_pctb"]) if pd.notna(row.get("bb_pctb")) else None,
                "vol_ratio20": float(row["Volume"] / vol_ma20.iloc[pos]) if pd.notna(vol_ma20.iloc[pos]) and vol_ma20.iloc[pos] else None,
                "gap_entry": ((nxt_open - close) / close * 100) if nxt_open else None,
                "n_strats": float(len(active)),
                "macro_level": None if mlev is None else float(mlev),
                "sector_regime_num": None if sreg_v is None else float(sreg_v),
                "horizon": horizon or None,
                "stock_regime": f"R{int(row['Regime'])}" if pd.notna(row.get("Regime")) else None,
                "regime_trans": _trans(df, pos),
                "macro_regime": None if mlev is None else f"L{mlev}",
                "sector_regime": None if sreg_v is None else f"R{sreg_v}",
                "sector_etf": sec_etf if sec_etf != "-" else None,
                "has_money": "있음" if float(row.get("Money_Score", 0)) >= 1.5 else "없음",
                "weekday": WEEKDAYS[ts.weekday()], "month": ts.strftime("%Y-%m"),
                "gap_dir": None if nxt_open is None else ("갭업" if nxt_open > close * 1.003 else "갭다운" if nxt_open < close * 0.997 else "보합"),
                "strategies": [a["name"] for a in active],
                "exit_type": out.exit_type, "exit_date": out.exit_date, "ret_pct": out.ret_pct,
                "mae_pct": out.mae_pct, "mfe_pct": out.mfe_pct, "days_held": out.days_held,
            }
            for h in FWD_HORIZONS:                         # 단순 보유 수익(배리어 무관), t+1 시가 → t+1+h 종가
                j = pos + 1 + h
                s[f"fwd{h}"] = ((float(df.iloc[j]["Close"]) / nxt_open - 1) * 100
                                if (nxt_open and j < len(df)) else None)
            for name in STRATEGY_NAMES:
                s[f"strat_{name}"] = "포함" if name in s["strategies"] else "미포함"
            sigs.append(s)

    # 그날 순위·폭(운영은 상위 15 만 추천) — 신호를 다 만든 뒤 날짜별로 매긴다
    by_day: dict[str, list[dict]] = defaultdict(list)
    for s in sigs:
        by_day[s["date"]].append(s)
    for day, lst in by_day.items():
        lst.sort(key=lambda x: -x["effective_score"])
        for i, s in enumerate(lst, 1):
            s["day_rank"], s["day_breadth"] = float(i), float(len(lst))
            s["top15"] = "안" if i <= TOP_N_FOR_AGENTS else "밖"
    if top_n and top_n > 0:
        sigs = [s for s in sigs if s["day_rank"] <= top_n]
    sigs.sort(key=lambda s: (s["date"], s["day_rank"]))
    return sigs


def _trans(df: pd.DataFrame, pos: int, lookback: int = 5) -> Optional[str]:
    if "Regime" not in df.columns or pos < lookback:
        return None
    cur, prev = df["Regime"].iloc[pos], df["Regime"].iloc[pos - lookback + 1]
    if pd.isna(cur) or pd.isna(prev):
        return None
    return "개선" if cur > prev else "악화" if cur < prev else "유지"


# ── 4. 실행 ───────────────────────────────────────────────────────────────
def run(market: Market, days: int = 365, top_n: int = TOP_N_FOR_AGENTS,
        universe: Optional[dict[str, str]] = None, prices: Optional[dict[str, pd.DataFrame]] = None,
        index_prices: Optional[dict[str, pd.DataFrame]] = None,
        sector_prices: Optional[dict[str, pd.DataFrame]] = None,
        win_pct: float = WIN_PCT, accept_pct: float = ACCEPT_PCT, min_n: int = 15) -> dict[str, Any]:
    """전체 파이프라인. 데이터 인자는 테스트 주입용(없으면 유니버스·캐시·다운로드)."""
    _quiet_pandas()
    t0 = time.time()
    cfg = get_config(market)
    from stock_auto.config.clock import market_now
    now = market_now(market)
    start_day = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    load_from = (now - timedelta(days=days + 420)).strftime("%Y-%m-%d")   # 지표·12개월 모멘텀 여유

    uni = universe if universe is not None else load_universe(market)
    syms = list(uni.keys())
    log(f"{market.value} 유니버스 {len(syms)}종목 · 신호 구간 {start_day}~ · 데이터 {load_from}~")
    prices = prices if prices is not None else load_prices(syms, market, load_from)
    index_prices = index_prices if index_prices is not None else load_prices(list(cfg.index_symbols), market, load_from)
    sector_etfs = sorted({e for e in uni.values() if e and e != "-"})
    sector_prices = sector_prices if sector_prices is not None else (load_prices(sector_etfs, market, load_from) if sector_etfs else {})
    log(f"가격 {len(prices)}종목 · 지수 {len(index_prices)} · 섹터 ETF {len(sector_prices)} ({time.time() - t0:.0f}s)")

    enriched: dict[str, pd.DataFrame] = {}
    fails = 0
    for s, df in prices.items():
        try:
            e = enrich(df, market)
            if e is not None:
                enriched[s] = e
        except Exception as ex:  # noqa: BLE001
            fails += 1
            if fails <= 5:
                log(f"{s} 지표 실패: {type(ex).__name__}: {str(ex)[:80]}")
    log(f"지표 계산 {len(enriched)}종목 (실패 {fails}) ({time.time() - t0:.0f}s)")
    macro = macro_series(index_prices, market)
    sector_reg = {}
    for etf, df in sector_prices.items():
        try:
            r = regime_series(df, market)
            if r is not None:
                sector_reg[etf] = r
        except Exception:  # noqa: BLE001
            pass
    mom, rank = momentum_rank_table(enriched)
    sigs = build_signals(enriched, market, start_day, uni, macro, sector_reg, mom, rank, top_n)
    log(f"신호 {len(sigs)}건 (top_n={top_n}) ({time.time() - t0:.0f}s)")

    cats = CATEGORICAL + [(f"strat_{n}", f"전략 {n}") for n in STRATEGY_NAMES]
    res = A.analyze_trades(sigs, NUMERIC, cats, OUTCOME_SIDE, min_n=min_n, win_pct=win_pct,
                           accept_pct=accept_pct, title_scope=f"{market.value} 백테스트 {days}일")
    by_strat = _by_key(sigs, "strategies", win_pct, multi=True)
    res.update({
        "backtest": True, "market_code": market.value, "days": None, "span": f"{start_day} ~ {now:%Y-%m-%d}",
        "n_universe": len(syms), "n_priced": len(prices), "top_n": top_n,
        "monthly": _monthly(sigs), "by_strategy": by_strat,
        "by_horizon": _by_key(sigs, "horizon", win_pct), "by_exit": _by_key(sigs, "exit_type", win_pct),
        "forward": _forward(sigs), "top15_vs_rest": _by_key(sigs, "top15", win_pct),
        "scenarios": filter_scenarios(sigs, res, win_pct, by_strat),
        "elapsed_s": round(time.time() - t0),
    })
    return res


# ── 4-2. 필터 시나리오 — "손실 쪽 특이점을 피했다면" ───────────────────────
def _half_split(sigs: list[dict]) -> tuple[list[dict], list[dict]]:
    dates = sorted({s["date"] for s in sigs})
    if len(dates) < 2:
        return sigs, []
    mid = dates[len(dates) // 2]
    return [s for s in sigs if s["date"] < mid], [s for s in sigs if s["date"] >= mid]


def filter_scenarios(sigs: list[dict], res: dict, win_pct: float,
                     by_strategy: Optional[list[dict]] = None) -> list[dict]:
    """손실 쪽 특이점(top_loss)을 하나씩, 그리고 전부 제외했을 때 남는 신호의 성과.

    특이점 표는 '어디에 손실이 몰렸나'를 보여 주지만, 그걸 **피하면 실제로 좋아지나**는
    따로 세어야 한다(제외한 구간에 수익도 같이 들어 있을 수 있다). 과적합 경계로 구간을
    앞/뒤 반으로 갈라 양쪽에서 모두 좋아지는지 함께 적는다 — 한쪽만 좋아지면 우연일 가능성.
    """
    top_loss = list(res.get("top_loss") or [])
    if not sigs or not top_loss:
        return []
    h1, h2 = _half_split(sigs)
    # id() 기반 집합 — dict 는 해시 불가
    set_h1 = {id(s) for s in h1}
    set_h2 = {id(s) for s in h2}

    def _in_half(keep: list[dict], ids: set[int]) -> list[dict]:
        return [s for s in keep if id(s) in ids]

    def _row2(name: str, keep: list[dict], excluded: int) -> dict:
        st = _stats(keep, win_pct)
        st.update({"name": name, "excluded": excluded, "sum": sum(s["ret_pct"] for s in keep),
                   "h1": _stats(_in_half(keep, set_h1), win_pct), "h2": _stats(_in_half(keep, set_h2), win_pct)})
        return st

    out = [_row2("기준(필터 없음)", sigs, 0)]
    # 월 단위 특이점은 미래에 쓸 수 없는 필터라 뺀다
    usable = [f for f in top_loss if f["factor"] != "month"][:5]
    for f in usable:
        keep = [s for s in sigs if not A._in_bucket(s, f)]
        out.append(_row2(f"제외: {f['label']}={f['bucket']}", keep, len(sigs) - len(keep)))
    keep = [s for s in sigs if not any(A._in_bucket(s, f) for f in usable)]
    out.append(_row2("제외: 위 전부(누적)", keep, len(sigs) - len(keep)))
    # 전략 선택 — PF 가 가장 좋은 전략 2개만 남기면
    bs = [r for r in (by_strategy or res.get("by_strategy") or []) if r["n"] >= max(30, len(sigs) * 0.05)]
    bs.sort(key=lambda r: -(r["pf"] if r["pf"] != float("inf") else 99))
    if len(bs) >= 2:
        names = {bs[0]["key"], bs[1]["key"]}
        keep = [s for s in sigs if names & set(s.get("strategies") or [])]
        out.append(_row2(f"전략 {'/'.join(sorted(names))} 포함만", keep, len(sigs) - len(keep)))
        keep2 = [s for s in keep if not any(A._in_bucket(s, f) for f in usable)]
        out.append(_row2("전략 선택 + 손실 특이점 전부 제외", keep2, len(sigs) - len(keep2)))
    return out


def _stats(lst: list[dict], win_pct: float) -> dict[str, Any]:
    n = len(lst)
    if not n:
        return {"n": 0}
    rets = [t["ret_pct"] for t in lst]
    wins = [r for r in rets if r >= win_pct and r > 0]
    losses = [r for r in rets if r < 0]
    gw, gl = sum(wins), abs(sum(losses))
    return {"n": n, "win_rate": len(wins) / n * 100, "loss_rate": len(losses) / n * 100,
            "avg": sum(rets) / n, "pf": (gw / gl) if gl else float("inf"),
            "tp": sum(1 for t in lst if t["exit_type"] == "tp"), "sl": sum(1 for t in lst if t["exit_type"] == "sl"),
            "to": sum(1 for t in lst if t["exit_type"] == "timeout")}


def _by_key(sigs: list[dict], key: str, win_pct: float, multi: bool = False) -> list[dict]:
    g: dict[str, list[dict]] = defaultdict(list)
    for s in sigs:
        v = s.get(key)
        if multi:
            for x in (v or []):
                g[str(x)].append(s)
        elif v not in (None, ""):
            g[str(v)].append(s)
    out = []
    for k, lst in g.items():
        st = _stats(lst, win_pct)
        st["key"] = k
        out.append(st)
    out.sort(key=lambda x: -x["n"])
    return out


def _monthly(sigs: list[dict]) -> list[dict]:
    g: dict[str, list[float]] = defaultdict(list)
    for s in sigs:
        g[s["month"]].append(s["ret_pct"])
    return [{"month": m, "n": len(v), "avg": sum(v) / len(v), "sum": sum(v),
             "win2": sum(1 for r in v if r >= WIN_PCT) / len(v) * 100} for m, v in sorted(g.items())]


def _forward(sigs: list[dict]) -> list[dict]:
    out = []
    for h in FWD_HORIZONS:
        v = [s[f"fwd{h}"] for s in sigs if s.get(f"fwd{h}") is not None]
        if v:
            out.append({"h": h, "n": len(v), "avg": sum(v) / len(v), "median": A._median(v),
                        "win2": sum(1 for r in v if r >= WIN_PCT) / len(v) * 100,
                        "pos": sum(1 for r in v if r > 0) / len(v) * 100})
    return out


# ── 5. 글 ─────────────────────────────────────────────────────────────────
def _pf(v: float) -> str:
    return "∞" if v == float("inf") else f"{v:.2f}"


def markdown(res: dict) -> str:
    L = [f"# [백테스트] {res['market_code']} 규칙엔진 신호 1년 — 수익(≥{res['win_pct']:g}%) vs 손실 특이점", "",
         f"- 구간 {res['span']} · 유니버스 {res['n_universe']}종목(가격 확보 {res['n_priced']}) · "
         f"그날 Effective 상위 {res['top_n'] or '전체'} · 소요 {res['elapsed_s']}s",
         f"- 진입 = 신호 다음날 시가, 청산 = 전략 ATR 손절/목표 또는 보유기간(단타3·중단기7·스윙20) 만료 — 운영 라벨러와 동일",
         "- LLM 단계(확신도·진입 조건·BUY/WATCH)는 과거에 없어 제외. 섹터 상태는 섹터 ETF 의 그날 레짐으로 대신. 실적 게이트 없음.",
         "- ⚠️ 유니버스는 오늘의 지수 구성종목 — 생존 편향 있음(1년 내 편입·퇴출 종목 제외). 수치는 방향 참고용.", ""]
    L += ["## 0. 요약", ""] + [f"- {x}" for x in A._head(res)] + [""]
    if res.get("note"):
        return "\n".join(L + [f"> {res['note']}", ""])
    # 핵심: 특이점
    body = A.markdown(res).split("\n")
    # A.markdown 의 머리말(제목·요약) 은 위에서 썼으니 '## 1.' 부터 붙인다
    i = next((k for k, ln in enumerate(body) if ln.startswith("## 1.")), 0)
    L += body[i:]
    # 추가 표
    L += ["## 5. 전략별 (한 신호에 여러 전략이 겹칠 수 있음)", "",
          "| 전략 | 건수 | 수익(≥2%) 비율 | 손실 비율 | 평균 | PF | TP/SL/TO |", "|---|---:|---:|---:|---:|---:|---|"]
    for r in res["by_strategy"]:
        L.append(f"| {r['key']} | {r['n']} | {r['win_rate']:.0f}% | {r['loss_rate']:.0f}% | {r['avg']:+.2f}% | {_pf(r['pf'])} | {r['tp']}/{r['sl']}/{r['to']} |")
    L += ["", "## 6. 보유기간 · 그날 순위 · 청산 유형", "",
          "| 구분 | 값 | 건수 | 수익(≥2%) 비율 | 평균 | PF |", "|---|---|---:|---:|---:|---:|"]
    for title, key in (("보유기간", "by_horizon"), ("상위 15", "top15_vs_rest"), ("청산 유형", "by_exit")):
        for r in res[key]:
            L.append(f"| {title} | {r['key']} | {r['n']} | {r['win_rate']:.0f}% | {r['avg']:+.2f}% | {_pf(r['pf'])} |")
    L += ["", "## 7. 단순 보유 수익(배리어 무관, 다음날 시가 → D+h 종가)", "",
          "| D+h | 건수 | 평균 | 중앙값 | ≥2% 비율 | 양수 비율 |", "|---|---:|---:|---:|---:|---:|"]
    for r in res["forward"]:
        L.append(f"| D+{r['h']} | {r['n']} | {r['avg']:+.2f}% | {r['median']:+.2f}% | {r['win2']:.0f}% | {r['pos']:.0f}% |")
    L += ["", "## 8. 월별", "", "| 월 | 건수 | 평균 | 합계 | ≥2% 비율 |", "|---|---:|---:|---:|---:|"]
    for r in res["monthly"]:
        L.append(f"| {r['month']} | {r['n']} | {r['avg']:+.2f}% | {r['sum']:+.1f}% | {r['win2']:.0f}% |")
    L += ["", "## 9. 필터 시나리오 — 손실 특이점을 피했다면", "",
          "같은 1년에서 해당 구간을 **빼고** 남은 신호의 성과. 앞/뒤 반기는 과적합 경계 — 양쪽 모두 좋아져야 믿을 만하다. "
          "월 특이점은 미래에 쓸 수 없어 제외.", "",
          "| 시나리오 | 제외 | 남은 건수 | ≥2% 비율 | 평균 | PF | 합계 | 전반 평균/PF | 후반 평균/PF |",
          "|---|---:|---:|---:|---:|---:|---:|---|---|"]
    for r in res.get("scenarios") or []:
        h1, h2 = r.get("h1") or {}, r.get("h2") or {}
        f1 = f"{h1['avg']:+.2f}%/{_pf(h1['pf'])} ({h1['n']})" if h1.get("n") else "-"
        f2 = f"{h2['avg']:+.2f}%/{_pf(h2['pf'])} ({h2['n']})" if h2.get("n") else "-"
        L.append(f"| {r['name']} | {r['excluded']} | {r['n']} | {r['win_rate']:.0f}% | {r['avg']:+.2f}% | "
                 f"{_pf(r['pf'])} | {r['sum']:+.0f}% | {f1} | {f2} |")
    L.append("")
    return "\n".join(L)


def telegram_text(res: dict) -> str:
    head = [f"🧪 [백테스트] {res['market_code']} 규칙엔진 신호 1년 ({res['span']})",
            f"유니버스 {res['n_universe']}종목 · 그날 상위 {res['top_n'] or '전체'} · 생존편향 주의"]
    body = A.telegram_text(res).split("\n")[1:]     # 첫 줄 제목만 바꾼다
    L = head + body
    if res.get("by_strategy"):
        L.append("")
        L.append("🧰 전략별(건수·≥2% 비율·평균)")
        for r in res["by_strategy"][:6]:
            L.append(f"· {r['key']}: {r['n']}건 {r['win_rate']:.0f}% {r['avg']:+.2f}%")
    fw = res.get("forward") or []
    if fw:
        L.append("")
        L.append("📈 단순 보유: " + " · ".join(f"D+{r['h']} {r['avg']:+.2f}%({r['win2']:.0f}%≥2%)" for r in fw))
    sc = res.get("scenarios") or []
    if len(sc) >= 2:
        L.append("")
        L.append("🧹 손실 특이점을 피했다면(기준 → 전부 제외 → 전략 선택+제외)")
        for r in [sc[0], next((x for x in sc if x["name"].startswith("제외: 위 전부")), None),
                  next((x for x in sc if x["name"].startswith("전략 선택 +")), None)]:
            if r:
                h1, h2 = r.get("h1") or {}, r.get("h2") or {}
                L.append(f"· {r['name']}: {r['n']}건 ≥2% {r['win_rate']:.0f}% 평균 {r['avg']:+.2f}% PF {_pf(r['pf'])}"
                         + (f" (전반 {h1['avg']:+.2f}% / 후반 {h2['avg']:+.2f}%)" if h1.get("n") and h2.get("n") else ""))
    return "\n".join(L)


# ── 6. 보고서 저장·게시 ────────────────────────────────────────────────────
def report_path(market: Market) -> Path:
    from stock_auto.ui import status as ui_status
    return ui_status.ui_dir() / f"backtest_{market.value}.md"


def publish(res: dict, market: Market, telegram: bool, notion: bool) -> None:
    from stock_auto.config.env import get_secrets
    md, short = markdown(res), telegram_text(res)
    p = report_path(market)
    p.write_text(md, encoding="utf-8")
    log(f"보고서 → {p}")
    sec = get_secrets()
    if telegram:
        from stock_auto.notify.telegram import for_market
        tg = for_market(market, sec)
        ok = bool(getattr(tg, "enabled", False)) and tg.send_message(short, parse_mode="")
        log("텔레그램 " + ("전송" if ok else "미설정/실패"))
    if notion:
        if not (sec.has_notion and sec.notion_parent_page_id):
            log("Notion 미설정")
        else:
            from stock_auto.integrations.notion_publisher import NotionPublisher
            pid = NotionPublisher(token=sec.notion_token).create_page_from_markdown(
                sec.notion_parent_page_id,
                f"[백테스트] {market.value} 1년 추천 요인 분석 ({datetime.now():%Y-%m-%d})", md)
            log("Notion 게시 " + ("완료" if pid else "실패"))


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="1년 백테스트 + 수익/손실 특이점")
    ap.add_argument("--market", default="US")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--top-n", type=int, default=TOP_N_FOR_AGENTS, help="그날 Effective 상위 N 만(0=전부)")
    ap.add_argument("--win-pct", type=float, default=WIN_PCT)
    ap.add_argument("--accept-pct", type=float, default=ACCEPT_PCT)
    ap.add_argument("--min-n", type=int, default=15)
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--notion", action="store_true")
    ap.add_argument("--short", action="store_true")
    a = ap.parse_args(argv)
    from stock_auto.config.env import load_env
    load_env()
    market = Market(a.market.upper())
    try:
        res = run(market, days=a.days, top_n=a.top_n, win_pct=a.win_pct, accept_pct=a.accept_pct, min_n=a.min_n)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        log(f"실패: {type(e).__name__}: {e}")
        _mark(market, f"fail {type(e).__name__}: {str(e)[:200]}")
        return 1
    print(telegram_text(res) if a.short else markdown(res))
    publish(res, market, a.telegram, a.notion)
    _mark(market, f"ok n={res['n']}")
    return 0


def _mark(market: Market, text: str) -> None:
    """데몬의 '보고서가 한 번도 없으면 시작 때 돈다' 판단용 표식(성공·실패 모두 남겨 반복 방지)."""
    try:
        from stock_auto.ui import status as ui_status
        (ui_status.ui_dir() / f"backtest_{market.value}.done").write_text(
            f"v{REPORT_VERSION} {datetime.now():%Y-%m-%d %H:%M} {text}", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    raise SystemExit(main())
