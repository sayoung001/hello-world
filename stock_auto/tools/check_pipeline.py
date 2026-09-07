"""
파이프라인 자가점검 — "지수 수집 · 유니버스 · 추천 산출"이 실제로 되는지 확인.

이 스크립트는 **SSH 서버에서 실행해야 의미가 있다.** 지수/일봉은 외부 API에서 오므로
네트워크가 막힌 환경에서는 수집 단계가 전부 실패로 나온다.

사용:
  python -m stock_auto.tools.check_pipeline              # 전 단계 점검
  python -m stock_auto.tools.check_pipeline --quick      # 지수·유니버스만
  python -m stock_auto.tools.check_pipeline --symbols 30 # 일봉 수집 표본 수 조절

점검 항목:
  1. 지수 수집     — S&P500(US500) / 나스닥종합(IXIC) / QQQ 일봉이 받아지는가
  2. 매크로 레짐   — 세 지수로 레짐이 계산되고 게이트 판정이 나오는가
  3. 유니버스      — 지수 구성종목이 몇 개나 잡히는가(샘플 폴백이면 경고)
  4. 일봉 수집     — 표본 종목의 일봉이 받아지고 마지막 봉이 최신인가
  5. 섹터 진단     — 섹터 ETF 12개 상태가 나오는가
  6. 실적 캘린더   — 표본 종목의 실적 일정이 받아지는가
  7. 추천 산출     — 스크리너가 후보를 만들고 기록 필드가 채워지는가
"""

from __future__ import annotations

import argparse
from typing import Optional

OK, WARN, BAD = "✅", "⚠️ ", "❌"


class Check:
    def __init__(self):
        self.rows: list[tuple[str, str, str]] = []

    def add(self, mark: str, name: str, detail: str = "") -> None:
        self.rows.append((mark, name, detail))
        print(f"{mark} {name}" + (f" — {detail}" if detail else ""))

    def summary(self) -> int:
        n_bad = sum(1 for m, _, _ in self.rows if m == BAD)
        n_warn = sum(1 for m, _, _ in self.rows if m == WARN)
        print("\n" + "─" * 60)
        print(f"점검 {len(self.rows)}건 — 정상 {len(self.rows) - n_bad - n_warn} · "
              f"경고 {n_warn} · 실패 {n_bad}")
        if n_bad:
            print("\n실패 항목:")
            for m, name, detail in self.rows:
                if m == BAD:
                    print(f"  - {name}: {detail}")
        return 1 if n_bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="파이프라인 자가점검")
    ap.add_argument("--market", choices=["US", "KR"], default="US")
    ap.add_argument("--quick", action="store_true", help="지수·유니버스만 점검")
    ap.add_argument("--symbols", type=int, default=20,
                    help="일봉/실적 수집 점검 표본 종목 수")
    args = ap.parse_args()

    from stock_auto.config.settings import Market, get_config
    from stock_auto.config.clock import market_today
    market = Market(args.market)
    cfg = get_config(market)
    c = Check()

    print(f"=== {market.value} 파이프라인 자가점검 "
          f"({market_today(market)} 거래소 기준) ===\n")

    # ── 1. 지수 수집 ──
    print("[1] 지수 수집")
    from stock_auto.data.downloader import load_or_download, expected_last_bar
    want_last = expected_last_bar(market)
    index_ohlcv = {}
    for sym in cfg.index_symbols:
        try:
            df = load_or_download(sym, _start(market), None, market)
            last = df.index[-1].date()
            fresh = last >= want_last.date()
            index_ohlcv[sym] = df
            c.add(OK if fresh else WARN, f"지수 {sym}",
                  f"{len(df)}봉 · 마지막 {last}"
                  + ("" if fresh else f" (기대 {want_last.date()} — 지연/휴장 확인)"))
        except Exception as e:  # noqa: BLE001
            c.add(BAD, f"지수 {sym}", f"{type(e).__name__}: {str(e)[:90]}")

    # ── 2. 매크로 레짐 ──
    print("\n[2] 매크로 레짐 · 게이트")
    if index_ohlcv:
        from stock_auto.strategy.macro_regime import compute_macro_regime, macro_gate_ok
        macro = compute_macro_regime(index_ohlcv, market)
        gate = macro_gate_ok(macro)
        c.add(OK if macro.per_index else BAD, "레짐 산출",
              f"L{macro.level}({macro.label}) · {macro.detail} · "
              f"그룹 {macro.per_group} · 매수게이트 {'통과' if gate else '차단'}")
        missing = [s for s in cfg.index_symbols if s not in macro.per_index]
        if missing:
            c.add(WARN, "레짐 미산출 지수", f"{missing} (60봉 미만이거나 수집 실패)")
    else:
        c.add(BAD, "레짐 산출", "지수 데이터가 하나도 없어 계산 불가")

    # ── 3. 유니버스 ──
    print("\n[3] 유니버스")
    from stock_auto.config.universe import resolve_universe, load_universe
    uni = resolve_universe(market)
    n = len(uni)
    sample_n = len(load_universe(market))
    if n <= sample_n:
        c.add(BAD, "유니버스 규모", f"N={n} — 샘플 폴백으로 보입니다. "
              "라이브 조회 실패 시 표본 축적이 24개월로 늘어납니다")
    elif n < 100:
        c.add(WARN, "유니버스 규모", f"N={n} — 100종목 미만. 표본 축적이 느립니다")
    else:
        c.add(OK, "유니버스 규모", f"N={n}종목")
    etfs = sorted({v for v in uni.values() if v and v != "-"})
    c.add(OK if len(etfs) >= 5 else WARN, "섹터 ETF 매핑",
          f"{len(etfs)}개 섹터: {', '.join(etfs[:12])}")

    if args.quick:
        return c.summary()

    syms = list(uni)[:args.symbols]

    # ── 4. 일봉 수집 ──
    print(f"\n[4] 일봉 수집 (표본 {len(syms)}종목)")
    ohlcv = {}
    fails = []
    stale = []
    for s in syms:
        try:
            df = load_or_download(s, _start(market), None, market)
            ohlcv[s] = df
            if df.index[-1].date() < want_last.date():
                stale.append(f"{s}({df.index[-1].date()})")
        except Exception as e:  # noqa: BLE001
            fails.append(f"{s}: {type(e).__name__}")
    c.add(OK if not fails else (WARN if len(fails) < len(syms) // 4 else BAD),
          "일봉 수집", f"성공 {len(ohlcv)}/{len(syms)}"
          + (f" · 실패 {fails[:5]}" if fails else ""))
    if stale:
        c.add(WARN, "일봉 신선도", f"{len(stale)}종목이 기대 봉({want_last.date()})보다 과거: "
              f"{stale[:5]}")
    # 소스 혼합 점검 — 종목마다 수정주가 정책이 다르면 횡단면 비교가 어긋난다
    from stock_auto.data.downloader import cache_sources
    srcs = cache_sources(market)
    if len(srcs) > 1:
        c.add(WARN, "데이터 소스 혼합", f"{srcs} — 수정주가 정책이 달라 "
              "종목 간 점수 비교가 어긋날 수 있습니다. 캐시 재구축 권장")
    elif srcs:
        c.add(OK, "데이터 소스", f"{srcs} (단일)")

    # ── 5. 섹터 진단 ──
    print("\n[5] 섹터 진단")
    try:
        from stock_auto.sector.sector_status import compute_sector_status
        scores, labels = compute_sector_status(market)
        c.add(OK if scores else BAD, "섹터 상태",
              f"{len(scores)}개 진단" if scores else "0개 — ETF 수집 실패")
        if scores:
            blocked = [k for k, v in scores.items() if v == 0]
            c.add(OK, "섹터 게이트", f"차단 섹터 {blocked or '없음'}")
    except Exception as e:  # noqa: BLE001
        c.add(BAD, "섹터 상태", f"{type(e).__name__}: {str(e)[:90]}")
        scores = {}

    # ── 6. 실적 캘린더 ──
    print("\n[6] 실적 캘린더")
    earn_blocked = earn_days = {}
    if market == Market.US:
        try:
            from stock_auto.data import earnings
            earnings.refresh(syms)
            earn_blocked, earn_days = earnings.blocked_map(syms)
            known = sum(1 for v in earn_days.values() if v is not None)
            c.add(OK if known else BAD, "실적 일정 수집",
                  f"확인 {known}/{len(syms)}종목 · " +
                  earnings.summary_line(earn_blocked, earn_days))
        except Exception as e:  # noqa: BLE001
            c.add(BAD, "실적 일정 수집", f"{type(e).__name__}: {str(e)[:90]}")
    else:
        c.add(WARN, "실적 일정 수집", "KR은 미지원(확장성용)")

    # ── 7. 추천 산출 ──
    print("\n[7] 추천 산출")
    if not ohlcv:
        c.add(BAD, "스크리너", "일봉이 없어 실행 불가")
        return c.summary()
    from stock_auto.strategy.screener import screen_universe
    scr = screen_universe(ohlcv, market, index_ohlcv=index_ohlcv,
                          stock_sector_etf=uni, sector_status=scores or None,
                          earnings_blocked=earn_blocked or None,
                          earnings_days=earn_days or None)
    c.add(OK if not scr.scored_all.empty else BAD, "스크리너 실행",
          f"채점 {len(scr.scored_all)} · 후보 {len(scr.candidates)} · "
          f"차단 {0 if scr.gated is None else len(scr.gated)} · "
          f"실패 {len(scr.failures)} · 기준봉 {scr.as_of}")
    if scr.failures:
        c.add(WARN, "스크리너 실패 종목",
              ", ".join(f"{k}({v[:28]})" for k, v in list(scr.failures.items())[:5]))

    # 후보가 없어도 정상일 수 있다(게이트 차단·임계 미달). 필드 완전성만 본다.
    check_df = scr.candidates if not scr.candidates.empty else scr.scored_all.head(1)
    need = ["symbol", "bar_date", "close", "atr", "effective_score",
            "stop", "target", "rr_ratio", "styles", "gated_by"]
    row = check_df.iloc[0].to_dict()
    missing = [k for k in need if row.get(k) is None or str(row.get(k)) == "nan"]
    c.add(OK if not missing else WARN, "기록 필드 완전성",
          "전부 채워짐" if not missing else f"비어있음: {missing}")
    if scr.candidates.empty:
        c.add(WARN, "매수 후보", "0건 — 게이트 차단이거나 임계 미달입니다(오류 아님). "
              f"매크로 게이트: {'통과' if scr.macro.level >= 1 else '차단'}")
    else:
        top = scr.candidates.iloc[0]
        c.add(OK, "매수 후보 상위",
              f"{top['symbol']} Eff {top['effective_score']:.2f} · "
              f"손절 {top['stop']} · 목표 {top['target']} · {top['strategies']}")

    return c.summary()


def _start(market) -> str:
    from datetime import timedelta
    from stock_auto.config.clock import market_now
    return (market_now(market) - timedelta(days=400)).strftime("%Y-%m-%d")


if __name__ == "__main__":
    raise SystemExit(main())
