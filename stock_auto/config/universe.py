"""
유니버스 정의 — 스캔 대상 종목 + 섹터 ETF 매핑.

전략:
- 전체 유니버스 스캔은 '규칙 기반 무토큰'(사용자 정책) → 종목이 많아도 OK.
- 라이브: FinanceDataReader 상장목록 → 거래대금/시총 상위로 축소(fdr_universe).
- 오프라인/기본: 샘플 대형주(SAMPLE) + 수동 섹터 ETF 매핑으로 즉시 동작.

섹터 ETF 매핑은 sector_map.get_sector_etf(industry)로 산출하며, 라이브 상장목록의
Industry 컬럼이 필요하다. 샘플은 매핑을 함께 제공한다.
"""

from __future__ import annotations

from typing import Optional

from stock_auto.config.settings import Market

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


def load_universe(market: Market) -> dict[str, str]:
    """기본(샘플) 유니버스: {symbol: 섹터ETF}."""
    return dict(US_SAMPLE if market == Market.US else KR_SAMPLE)


def fdr_universe(market: Market, top_n: int = 300) -> dict[str, str]:
    """
    라이브: FinanceDataReader 상장목록 → 섹터 ETF 매핑.
    네트워크 필요. 실패 시 샘플로 폴백.
    """
    try:
        import FinanceDataReader as fdr
        from stock_auto.sector.sector_map import get_sector_etf

        if market == Market.US:
            df = fdr.StockListing("NASDAQ")
        else:
            df = fdr.StockListing("KRX")
        out: dict[str, str] = {}
        ind_col = next((c for c in ("Industry", "Sector", "industry")
                        if c in df.columns), None)
        sym_col = next((c for c in ("Symbol", "Code", "code") if c in df.columns), None)
        if sym_col is None:
            raise ValueError("심볼 컬럼 없음")
        for _, r in df.head(top_n).iterrows():
            sym = str(r[sym_col])
            industry = str(r[ind_col]) if ind_col else ""
            etf, _ = get_sector_etf(sym, industry)
            out[sym] = etf
        return out or load_universe(market)
    except Exception as e:  # noqa: BLE001
        print(f"[universe] FDR 상장목록 실패({type(e).__name__}) → 샘플 사용")
        return load_universe(market)
