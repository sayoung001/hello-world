"""
일일 배치 실행 진입점 (B).

사용:
  python -m stock_auto.pipeline.run_daily_batch --market US
  python -m stock_auto.pipeline.run_daily_batch --market KR --no-llm
  python -m stock_auto.pipeline.run_daily_batch --market US --sample-universe

흐름: .env 로드 → 유니버스(지수 구성종목) → 섹터 게이트 → 실적 게이트
      → run_daily(규칙스캔 → 추천 LLM → Notion 게시 + Telegram 다이제스트) → 요약 출력.

키 유무에 따라 자동 게이트:
  - ANTHROPIC_API_KEY 없음 → LLM 생략(규칙 스캔만)
  - NOTION_TOKEN 없음      → Notion 게시 생략
  - TELEGRAM_* 없음        → 다이제스트를 콘솔로 출력
"""

from __future__ import annotations

import argparse

from stock_auto.config.env import get_secrets
from stock_auto.config.settings import Market
from stock_auto.config.universe import load_universe, resolve_universe
from stock_auto.integrations.notion_publisher import NotionPublisher
from stock_auto.pipeline.daily_batch import run_daily


def main() -> int:
    ap = argparse.ArgumentParser(description="주식 일일 배치")
    ap.add_argument("--market", choices=["US", "KR"], default="US")
    ap.add_argument("--no-llm", action="store_true", help="LLM 추천 분석 생략")
    ap.add_argument("--sample-universe", action="store_true",
                    help="샘플 20종목만 사용(디버깅용). 기본은 지수 구성종목 라이브")
    ap.add_argument("--top-n", type=int, default=15)
    ap.add_argument("--no-sector", action="store_true",
                    help="섹터 게이트 비활성 (섹터 ETF 다운로드 생략)")
    ap.add_argument("--no-earnings", action="store_true",
                    help="실적 발표일 게이트 비활성")
    ap.add_argument("--no-telegram", action="store_true",
                    help="텔레그램 다이제스트 전송 생략")
    args = ap.parse_args()

    market = Market(args.market)
    sec = get_secrets()

    # 유니버스 + 섹터 ETF
    uni_map = (load_universe(market) if args.sample_universe
               else resolve_universe(market))
    symbols = list(uni_map.keys())
    print(f"[run] {market.value} 유니버스 N={len(symbols)}")
    if len(symbols) < 100 and not args.sample_universe:
        print("[run] ⚠️ 유니버스가 100종목 미만입니다 — 라이브 조회 실패 폴백일 수 있습니다. "
              "표본 축적 속도가 크게 떨어집니다(금융분석 문서 3장).")

    # LLM 게이트
    run_llm = sec.has_anthropic and not args.no_llm
    if not run_llm:
        print("[run] LLM 생략" +
              ("" if args.no_llm else " (ANTHROPIC_API_KEY 없음)"))

    # 섹터 게이트 — 섹터 ETF 진단 → 하락위험(0) 섹터 종목 매수 차단
    from stock_auto.sector.sector_status import (
        compute_sector_status, labels_for_symbols, summary_lines)
    sector_scores: dict[str, int] = {}
    sector_labels: dict[str, str] = {}
    if not args.no_sector:
        sector_scores, sector_labels = compute_sector_status(market)
        if sector_scores:
            print(f"[run] 섹터 게이트 ON — {len(sector_scores)}개 진단")
            for line in summary_lines(sector_scores, sector_labels, market):
                print(f"       {line}")
        else:
            print("[run] 섹터 진단 실패 — 게이트 비활성으로 진행")
    label_map = (labels_for_symbols(uni_map, sector_labels)
                 if sector_labels else None)

    # 실적 발표일 게이트 — 갭이 손절을 건너뛰는 구간을 진입에서 제외
    earn_blocked: dict = {}
    earn_days: dict = {}
    if not args.no_earnings and market == Market.US:
        from stock_auto.data import earnings
        try:
            earnings.refresh(symbols)
            earn_blocked, earn_days = earnings.blocked_map(symbols)
            print("[run] " + earnings.summary_line(earn_blocked, earn_days))
        except Exception as e:  # noqa: BLE001
            print(f"[run] 실적 캘린더 실패({type(e).__name__}) — 게이트 비활성으로 진행")

    # Notion
    notion = NotionPublisher(token=sec.notion_token) if sec.has_notion else None
    if notion:
        print(f"[run] Notion 게시 ON (DB={'설정됨' if sec.notion_reco_db_id else '미설정'})")

    result = run_daily(
        market=market,
        universe=symbols,
        stock_sector_etf=uni_map,
        sector_status=sector_scores or None,
        sector_label_map=label_map,
        sector_lines=(summary_lines(sector_scores, sector_labels, market)
                      if sector_scores else None),
        earnings_blocked=earn_blocked or None,
        earnings_days=earn_days or None,
        llm_client=None,                 # base가 .env의 키로 실제 생성
        send_telegram=not args.no_telegram,
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

    # 게이트에 막힌 원신호 — 기록은 되며, 게이트 실효성 검증에 쓰인다
    gated = result.screen.gated
    if gated is not None and not gated.empty:
        print(f"\n===== 게이트 차단 {len(gated)}건 (기록됨) =====")
        for r in gated.head(10).itertuples():
            de = getattr(r, "days_to_earnings", None)
            extra = f" · 실적 D-{de}" if de is not None and de == de else ""
            print(f"  [{r.gated_by}] {r.symbol} — Eff {r.effective_score}{extra}")
        print("  ※ 차단분도 라벨링됩니다 → 리포트에서 '게이트 통과분 vs 차단분' 비교")

    # 매도(청산) 신호 — 보유 종목 점검용
    exits = result.screen.exits
    print("\n===== 매도 신호 =====")
    if exits is not None and not exits.empty:
        for r in exits.itertuples():
            print(f"  [EXIT] {r.symbol} — Exit_Score {r.exit_score}/5 · 종가 {r.close:,.2f}")
        print("  ※ 보유 중인 종목이면 청산 조건을 점검하세요")
    else:
        print("  없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
