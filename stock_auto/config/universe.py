"""
유니버스 정의 — 스캔 대상 종목 + 섹터 ETF 매핑.

전략:
- 전체 유니버스 스캔은 '규칙 기반 무토큰'(사용자 정책) → 종목이 많아도 OK.
- 라이브(US): **지수 구성종목**을 유니버스로 쓴다 — S&P500 ∪ 나스닥100(QQQ 구성).
- 오프라인/기본: 샘플 대형주(SAMPLE) + 수동 섹터 ETF 매핑으로 즉시 동작.

★ 왜 '상장목록 앞 N개'가 아니라 '지수 구성종목'인가
  이전 구현은 `fdr.StockListing("NASDAQ").head(300)`이었다. 정렬이 없어 심볼
  알파벳순 앞 300개(= A로 시작하는 나스닥 마이크로캡)가 잡혔고, NYSE/AMEX는
  통째로 빠졌다. 그 종목군으로 쌓은 표본은 실제로 거래할 종목군을 설명하지 못한다.
  지수 구성종목은 (a) 유동성이 보장되고 (b) 거래소를 가리지 않으며
  (c) 리밸런싱을 지수 제공자가 대신해 준다.

스냅샷:
  유니버스는 시간에 따라 바뀐다(편입·편출). 매 조회를 날짜별 CSV로 남겨야
  나중에 "그때 그 신호가 어떤 유니버스에서 나왔는지"를 재현할 수 있다.
  → data/universe/universe_US_YYYY-MM-DD.csv + 최신본 universe_US.csv
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from stock_auto.config.settings import Market

from stock_auto.config.paths import universe_dir

# ── 샘플 유니버스(즉시 동작용 대형주) + 섹터 ETF ──
US_SAMPLE: dict[str, str] = {
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "AVGO": "XLK",
    "GOOGL": "XLC", "META": "XLC", "NFLX": "XLC",
    "AMZN": "XLY", "TSLA": "XLY", "HD": "XLY",
    "JPM": "XLF", "BAC": "XLF", "V": "XLF",
    "LLY": "XLV", "UNH": "XLV", "JNJ": "XLV",
    "XOM": "XLE", "CVX": "XLE",
    "CAT": "XLI", "GE": "XLI",
}

KR_SAMPLE: dict[str, str] = {
    "005930": "091160",  # 삼성전자 → 반도체
    "000660": "091160",  # SK하이닉스 → 반도체
    "373220": "305720",  # LG에너지솔루션 → 2차전지
    "207940": "266370",  # 삼성바이오로직스 → 바이오
    "005380": "091180",  # 현대차 → 자동차
    "051910": "117460",  # LG화학 → 에너지화학
    "105560": "091170",  # KB금융 → 은행
    "035420": "261060",  # NAVER → IT
}

# ── 나스닥100(QQQ) 구성 중 S&P500에 없는 종목 ──
# FinanceDataReader는 S&P500 구성종목은 제공하지만 나스닥100은 제공하지 않는다.
# 대부분의 NDX 종목은 S&P500에도 들어 있고, 빠지는 것은 주로 미국 외 법인
# (S&P500 편입 요건이 미국 법인이라서)이다. 그 차집합만 여기에 둔다.
#
# ⚠️ 이 목록은 지수 리밸런싱(연 1회, 12월)마다 낡는다. 분기마다 확인할 것.
#    누락되어도 시스템은 정상 동작하며 유니버스가 그만큼 줄어들 뿐이다.
NASDAQ100_EXTRA: dict[str, str] = {
    "AZN": "XLV",     # AstraZeneca (영국)
    "ARM": "XLK",     # Arm Holdings (영국)
    "PDD": "XLY",     # PDD Holdings (중국/아일랜드)
    "MELI": "XLY",    # MercadoLibre (아르헨티나/미국 상장)
    "SHOP": "XLK",    # Shopify (캐나다)
    "CSGP": "XLRE",   # CoStar Group
    "LIN": "XLB",     # Linde (아일랜드)
    "FTNT": "XLK",    # Fortinet
    "TEAM": "XLK",    # Atlassian (호주/영국)
    "CDW": "XLK",
    "DASH": "XLY",
    "TTD": "XLC",
    "ZS": "XLK",
    "DDOG": "XLK",
}


def load_universe(market: Market) -> dict[str, str]:
    """기본(샘플) 유니버스: {symbol: 섹터ETF}. 네트워크 불필요."""
    return dict(US_SAMPLE if market == Market.US else KR_SAMPLE)


# ── 라이브 유니버스 ────────────────────────────────────────────────────────
def us_universe(include_nasdaq100: bool = True,
                snapshot: bool = True) -> dict[str, str]:
    """
    미국 유니버스 = S&P500 구성종목 ∪ 나스닥100 차집합. 약 500~520종목.

    실패 시 (a) 마지막 스냅샷 CSV → (b) 샘플 순으로 폴백한다.
    폴백이 일어나면 반드시 로그로 알린다 — 조용히 20종목으로 줄어드는 것이
    가장 위험한 실패 모드다(표본 축적이 24개월로 늘어난다).
    """
    from stock_auto.sector.sector_map import get_sector_etf
    out: dict[str, str] = {}
    base_ok = False
    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing("S&P500")
        sym_col = _pick(df, ("Symbol", "Code", "symbol"))
        sec_col = _pick(df, ("Sector", "Industry", "sector", "industry"))
        if sym_col is None:
            raise ValueError(f"심볼 컬럼 없음 (컬럼: {list(df.columns)[:8]})")
        for _, r in df.iterrows():
            sym = str(r[sym_col]).strip().upper()
            if not sym or sym == "NAN":
                continue
            etf, _ = get_sector_etf(sym, str(r[sec_col]) if sec_col else "")
            out[sym] = etf
        base_ok = len(out) >= 100     # S&P500이면 최소 수백 개여야 정상
        print(f"[universe] S&P500 구성종목 {len(out)}개"
              + ("" if base_ok else " — 비정상적으로 적습니다"))
    except Exception as e:  # noqa: BLE001
        print(f"[universe] S&P500 조회 실패({type(e).__name__}: {str(e)[:120]})")

    # ★ 기반 목록(S&P500)이 실패했는데 보충 목록만 얹으면 14종목짜리 '가짜 유니버스'가
    #   만들어져 폴백이 동작하지 않는다. 기반이 성립했을 때만 보충한다.
    if include_nasdaq100 and base_ok:
        added = 0
        for sym, etf in NASDAQ100_EXTRA.items():
            if sym not in out:
                out[sym] = etf
                added += 1
        if added:
            print(f"[universe] 나스닥100 차집합 {added}개 추가")

    if not base_ok:
        cached = load_snapshot(Market.US)
        if cached:
            print(f"[universe] ⚠️ 라이브 실패 → 마지막 스냅샷 {len(cached)}종목 사용")
            return cached
        print("[universe] ⚠️ 라이브·스냅샷 모두 실패 → 샘플 20종목 사용 "
              "(표본 축적이 크게 느려집니다)")
        return load_universe(Market.US)

    if snapshot:
        save_snapshot(out, Market.US)
    return out


def fdr_universe(market: Market, top_n: Optional[int] = None) -> dict[str, str]:
    """
    라이브 유니버스. US는 지수 구성종목(us_universe), KR은 KRX 상장목록.

    top_n을 주면 그 개수로 자른다. 다만 US는 지수 구성종목이라 자를 이유가 거의 없고,
    자르면 순서가 지수 제공자 순서에 종속되므로 권장하지 않는다.
    """
    if market == Market.US:
        uni = us_universe()
    else:
        uni = _krx_universe()
    if top_n is not None and len(uni) > top_n:
        print(f"[universe] ⚠️ {len(uni)}종목 → 상위 {top_n}종목으로 절단 "
              "(절단 순서에 근거가 없으므로 권장하지 않음)")
        uni = dict(list(uni.items())[:top_n])
    return uni


def _krx_universe(top_n: int = 300) -> dict[str, str]:
    """KR 확장성용. 시가총액 상위로 정렬(컬럼이 있을 때)."""
    try:
        import FinanceDataReader as fdr
        from stock_auto.sector.sector_map import get_sector_etf
        df = fdr.StockListing("KRX")
        sym_col = _pick(df, ("Code", "Symbol", "code"))
        ind_col = _pick(df, ("Industry", "Sector", "industry"))
        cap_col = _pick(df, ("Marcap", "MarketCap", "marcap"))
        if sym_col is None:
            raise ValueError("심볼 컬럼 없음")
        if cap_col is not None:
            df = df.sort_values(cap_col, ascending=False)
        else:
            print("[universe] ⚠️ KRX 시총 컬럼 없음 — 정렬 없이 앞에서 자릅니다")
        out: dict[str, str] = {}
        for _, r in df.head(top_n).iterrows():
            sym = str(r[sym_col]).strip()
            etf, _ = get_sector_etf(sym, str(r[ind_col]) if ind_col else "")
            out[sym] = etf
        return out or load_universe(Market.KR)
    except Exception as e:  # noqa: BLE001
        print(f"[universe] KRX 상장목록 실패({type(e).__name__}) → 샘플 사용")
        return load_universe(Market.KR)


def _pick(df, names: tuple[str, ...]) -> Optional[str]:
    return next((c for c in names if c in df.columns), None)


# ── 스냅샷 ────────────────────────────────────────────────────────────────
def _snapshot_path(market: Market, date: Optional[str] = None) -> Path:
    name = (f"universe_{market.value}_{date}.csv" if date
            else f"universe_{market.value}.csv")
    return universe_dir() / name


MIN_SNAPSHOT_SIZE = 50


def save_snapshot(uni: dict[str, str], market: Market) -> Optional[Path]:
    """
    날짜별 스냅샷 + 최신본을 함께 남긴다(신호 재현용).

    비정상적으로 작은 유니버스는 저장하지 않는다 — 일시적 조회 실패로 만들어진
    반쪽짜리 목록을 스냅샷으로 굳히면, 이후 폴백이 그 잘못된 목록을 계속 쓴다.
    """
    import csv
    from stock_auto.config.clock import market_today
    if len(uni) < MIN_SNAPSHOT_SIZE:
        print(f"[universe] 스냅샷 저장 생략 — {len(uni)}종목은 비정상 "
              f"(최소 {MIN_SNAPSHOT_SIZE})")
        return None
    universe_dir().mkdir(parents=True, exist_ok=True)
    today = market_today(market)
    for p in (_snapshot_path(market, today), _snapshot_path(market)):
        with p.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "sector_etf"])
            w.writerows(sorted(uni.items()))
    return _snapshot_path(market)


def load_snapshot(market: Market, date: Optional[str] = None
                  ) -> Optional[dict[str, str]]:
    import csv
    p = _snapshot_path(market, date)
    if not p.exists():
        return None
    with p.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    uni = {r["symbol"]: r.get("sector_etf", "-") for r in rows}
    if len(uni) < MIN_SNAPSHOT_SIZE:
        print(f"[universe] 스냅샷이 {len(uni)}종목뿐이라 신뢰할 수 없습니다 — 무시")
        return None
    return uni or None


def resolve_universe(market: Market, live: bool = True) -> dict[str, str]:
    """
    운영 진입점 — 데몬·배치·모니터가 공통으로 부르는 함수.

    live=True면 지수 구성종목, False면 샘플. 환경변수 `STOCK_UNIVERSE=sample`로
    강제 샘플 전환이 가능하다(디버깅용).
    """
    if os.environ.get("STOCK_UNIVERSE", "").strip().lower() == "sample":
        print("[universe] STOCK_UNIVERSE=sample — 샘플 유니버스 사용")
        return load_universe(market)
    if not live:
        return load_universe(market)
    return fdr_universe(market)
