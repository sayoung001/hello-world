"""
일일 배치 실행 진입점 (B).

사용:
  python -m stock_auto.pipeline.run_daily_batch --market US
  python -m stock_auto.pipeline.run_daily_batch --market KR --no-llm
  python -m stock_auto.pipeline.run_daily_batch --market US --live-universe --top-n 15

흐름: .env 로드 → 유니버스 → run_daily(규칙스캔→추천 LLM→Notion 게시) → 요약 출력.

키 유무에 따라 자동 게이트:
  - ANTHROPIC_API_KEY 없음 → LLM 생략(규칙 스캔만)
  - NOTION_TOKEN 없음 → 게시 생략(콘솔 출력)
"""

from __future__ import annotations

import argparse

from stock_auto.config.env import get_secrets
from stock_auto.config.settings import Market
from stock_auto.config.universe import load_universe, fdr_universe
from stock_auto.integrations.notion_publisher import NotionPublisher
from stock_auto.pipeline.daily_batch import run_daily


def main() -> int:
    ap = argparse.ArgumentParser(description="주식 일일 배치")
    ap.add_argument("--market", choices=["US", "KR"], default="US")
    ap.add_argument("--no-llm", action="store_true", help="LLM 추천 분석 생략")
    ap.add_argument("--live-universe", action="store_true",
                    help="FDR 상장목록 사용(기본은 샘플)")
    ap.add_argument("--top-n", type=int, default=15)
    args = ap.parse_args()

    market = Market(args.market)
    sec = get_secrets()

    # 유니버스 + 섹터 ETF
    uni_map = (fdr_universe(market) if args.live_universe
               else load_universe(market))
    symbols = list(uni_map.keys())
    print(f"[run] {market.value} 유니버스 {len(symbols)}종목")

    # LLM 게이트
    run_llm = sec.has_anthropic and not args.no_llm
    if not run_llm:
        print("[run] LLM 생략" +
              ("" if args.no_llm else " (ANTHROPIC_API_KEY 없음)"))

    # Notion
    notion = NotionPublisher(token=sec.notion_token) if sec.has_notion else None
    if notion:
        print(f"[run] Notion 게시 ON (DB={'설정됨' if sec.notion_reco_db_id else '미설정'})")

    result = run_daily(
        market=market,
        universe=symbols,
        stock_sector_etf=uni_map,
        llm_client=None,                 # base가 .env의 키로 실제 생성
        notion=notion,
        notion_db_id=sec.notion_reco_db_id or None,
        notion_parent_page=sec.notion_parent_page_id or None,
        top_n=args.top_n,
        run_llm=run_llm,
    )

    # 콘솔 요약
    print("\n===== 추천 요약 =====")
    if result.agents.recommendations:
        for r in result.agents.recommendations:
            pnl = r.get("expected_pnl_today", {})
            print(f"  [{r.get('recommend')}] {r.get('ticker')} "
                  f"({r.get('horizon')}) 확신 {r.get('conviction')} | "
                  f"예상 P10 {pnl.get('p10_pct')}% / P50 {pnl.get('p50_pct')}% / "
                  f"P90 {pnl.get('p90_pct')}%")
            print(f"     진입: {r.get('entry_rule')}")
    elif not result.screen.candidates.empty:
        cols = ["symbol", "effective_score", "stock_regime", "styles"]
        print(result.screen.candidates[cols].to_string(index=False))
    else:
        print("  매수 후보 없음 (매크로/섹터 게이트 또는 임계 미달)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
