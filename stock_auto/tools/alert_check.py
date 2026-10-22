"""
알림 점검 — VM에 쌓인 실제 데이터로 "알림이 제대로 오는가"를 확인한다.

왜 따로 만드나:
  알림 배선을 확인할 방법이 지금까지 두 가지뿐이었다.
    · 배치가 돌 때까지 기다린다(하루 한 번)
    · 트리거를 실제로 발화시킨다 → **상태가 fired 로 소모되어** 정작
      실제 알림이 영영 오지 않는다
  그래서 먼저 **읽기만** 해서 무엇이 쌓여 있는지 보여주고, 그 다음에
  상태를 건드리지 않는 발송만 골라서 보낸다.

단계:
  1) 점검(기본)  — 읽기만. 신호·트리거·사용량·autopull 상태를 집계
  2) --dry-run   — 트리거가 지금 발화할지 판정(상태 저장 안 함, 전송 안 함)
  3) --send      — 실제 데이터로 만든 요약을 텔레그램으로 1건 발송
                   (트리거를 발화시키지 않는다 — 상태 불변)

사용:
  .venv/bin/python -m stock_auto.tools.alert_check
  .venv/bin/python -m stock_auto.tools.alert_check --dry-run
  .venv/bin/python -m stock_auto.tools.alert_check --send
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

OK, NO, WARN = "✅", "❌", "⚠️"


def _rows(fn, label: str) -> list[dict]:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        print(f"  {NO} {label} 읽기 실패: {type(e).__name__}: {e}")
        return []


def inspect() -> dict[str, Any]:
    """읽기만 — 아무것도 바꾸지 않는다."""
    from stock_auto.config import paths
    from stock_auto.config.env import get_secrets
    from stock_auto.tracking import store, triggers as T

    out: dict[str, Any] = {}
    sec = get_secrets()

    print("=" * 62)
    print("  알림 점검 — VM에 쌓인 실제 데이터 (읽기만)")
    print("=" * 62)
    print(f"\n데이터 경로: {paths.describe()}")

    # ── 알림 배선 ──
    print("\n── 알림 배선 ──")
    print(f"  {OK if sec.has_telegram else NO} 텔레그램 "
          f"{'설정됨' if sec.has_telegram else '미설정 — 알림이 콘솔로만 갑니다'}")
    print(f"  {OK if sec.has_notion else WARN} Notion "
          f"{'설정됨' if sec.has_notion else '미설정 — 트리거는 알림만 가고 정리는 생략'}")
    tdb = getattr(sec, "notion_trigger_db_id", "")
    print(f"  {OK if tdb else WARN} 진입 신호 도달 DB "
          f"{'설정됨' if tdb else '미설정 — 부모 페이지에 개별 페이지로 게시'}")
    out["telegram"] = sec.has_telegram

    # ── 신호 기록 ──
    print("\n── 신호 기록(signals.csv) ──")
    rows = _rows(store.load, "signals.csv")
    out["signals"] = len(rows)
    if not rows:
        print("  (비어 있음 — 배치가 아직 돌지 않았습니다)")
    else:
        closed = [r for r in rows if str(r.get("exit_type", "")).strip()
                  not in ("", "nodata")]
        llm = [r for r in rows if str(r.get("recommend", "")).strip()]
        dates = sorted({str(r.get("date", "")) for r in rows if r.get("date")})
        print(f"  총 {len(rows)}건 · LLM 추천 {len(llm)}건 · 청산완료 {len(closed)}건")
        print(f"  기간 {dates[0]} ~ {dates[-1]} ({len(dates)}일)")
        out["closed"] = len(closed)

    # ── 진입 트리거 ──
    print("\n── 진입 트리거(triggers.csv) ──")
    trows = _rows(T.load, "triggers.csv")
    out["triggers"] = len(trows)
    if not trows:
        print("  (비어 있음 — 트리거 기능이 들어간 뒤 배치가 아직 안 돌았거나,")
        print("   LLM 추천이 없어 등록할 조건이 없었습니다)")
    else:
        by: dict[str, int] = {}
        for r in trows:
            k = str(r.get("status", "?"))
            by[k] = by.get(k, 0) + 1
        print(f"  총 {len(trows)}건 — " + " · ".join(f"{k} {v}" for k, v in sorted(by.items())))
        pend = [r for r in trows if r.get("status") == "pending"]
        out["pending"] = len(pend)
        for r in pend[:8]:
            print(f"    • {r.get('date')} {r.get('symbol'):<6} "
                  f"{r.get('kind')} {r.get('value_pct')}% "
                  f"→ 도달가 {r.get('target_price')} "
                  f"(기준 {r.get('basis_price')}) "
                  f"{'분할' if str(r.get('split','')).lower() in ('yes','y','true','1') else ''}")
            print(f"      진입문구: {str(r.get('rule_text',''))[:70]}")
        if len(pend) > 8:
            print(f"    … 외 {len(pend) - 8}건")
        # 장중 감시가 실제로 체결을 받는지 — NYSE 종목을 NAS 코드로 구독하면
        # 아무것도 안 온다. 대기 종목의 거래소 매핑을 보여 준다.
        us_pend = sorted({r["symbol"] for r in pend if (r.get("market") or "US") == "US"})
        if us_pend:
            try:
                from stock_auto.data import us_exchange
                m = us_exchange.load_map(us_pend)
                print(f"  KIS {us_exchange.summary(us_pend, m)}")
                if len(m) < len(us_pend):
                    print("    ⚠️ 매핑 캐시 없음/부족 — 데몬이 개장 시 자동 갱신. 지금 받으려면:")
                    print("       .venv/bin/python -m stock_auto.data.us_exchange "
                          + " ".join(us_pend[:5]))
            except Exception as e:  # noqa: BLE001
                print(f"  {WARN} 거래소 매핑 확인 실패: {type(e).__name__}")
        fired = [r for r in trows if r.get("status") == "fired"]
        if fired:
            print(f"  이미 발화한 건 {len(fired)}개 — 가장 최근:")
            for r in fired[-3:]:
                print(f"    • {r.get('fired_at')} {r.get('symbol')} "
                      f"체결 {r.get('fired_price')}")

    # ── 토큰·비용 ──
    print("\n── LLM 사용량(usage.csv) ──")
    try:
        from stock_auto.tracking import usage_log
        for line in usage_log.section_md()[1:]:
            print(" " + line)
    except Exception as e:  # noqa: BLE001
        print(f"  읽기 실패: {type(e).__name__}: {e}")

    # ── autopull ──
    print("\n── 자동 반영(autopull) ──")
    root = Path(__file__).resolve().parents[2]
    beat = root / "logs" / "autopull.heartbeat"
    state = root / "logs" / "autopull_alert.state"
    if beat.exists():
        import time
        age = (time.time() - beat.stat().st_mtime) / 60.0
        mark = OK if age < 30 else WARN
        print(f"  {mark} 마지막 실행 {age:.0f}분 전")
    else:
        print(f"  {WARN} heartbeat 없음 — autopull 이 아직 안 돌았습니다")
    if state.exists():
        print(f"  {WARN} 중단 상태 남아 있음: {state.read_text(encoding='utf-8').strip()}")
    else:
        print(f"  {OK} 중단 상태 없음")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="알림 점검(실제 데이터)")
    ap.add_argument("--dry-run", action="store_true",
                    help="트리거가 지금 발화할지 판정(상태 저장 안 함, 전송 안 함)")
    ap.add_argument("--send", action="store_true",
                    help="실제 데이터 요약을 텔레그램으로 1건 발송(상태 불변)")
    args = ap.parse_args(argv)

    info = inspect()

    if args.dry_run:
        print("\n" + "=" * 62)
        print("  트리거 판정 (dry-run — 상태를 바꾸지 않습니다)")
        print("=" * 62)
        from stock_auto.tracking.trigger_eval import evaluate_daily, TriggerFirer

        class _Silent:
            enabled = False
            def send_message(self, *a, **k): return True
            def send_photo(self, *a, **k): return True

        st = evaluate_daily(firer=TriggerFirer(notion=None, telegram=_Silent(),
                                               send_chart=False),
                            dry_run=True)
        print(f"\n  발화 예정 {st['fired']} · 만료 예정 {st['expired']} · "
              f"대기 {st['pending']} · 일봉없음 {st['nodata']}")
        if st["fired"]:
            print("  ↑ 위에 찍힌 메시지가 실제로 폰에 갈 내용입니다.")
            print("  실제로 보내려면: .venv/bin/python -m stock_auto.pipeline.check_triggers")

    if args.send:
        print("\n" + "=" * 62)
        print("  텔레그램 발송 (실제 데이터 요약 — 트리거는 건드리지 않음)")
        print("=" * 62)
        if not info.get("telegram"):
            print(f"  {NO} 텔레그램 미설정 — .env 의 TELEGRAM_* 를 확인하세요")
            return 1
        from stock_auto.config.env import get_secrets
        from stock_auto.notify.telegram import Telegram
        sec = get_secrets()
        tg = Telegram(sec.telegram_bot_token, sec.telegram_chat_id)
        msg = _summary_message(info)
        ok = tg.send_message(msg, parse_mode="")
        print(f"  {OK if ok else NO} {'발송 완료 — 폰에서 확인하세요' if ok else '발송 실패'}")
        print("\n보낸 내용:\n" + msg)
        return 0 if ok else 1

    print("\n" + "=" * 62)
    print("다음 단계:")
    print("  판정만 보기   .venv/bin/python -m stock_auto.tools.alert_check --dry-run")
    print("  폰으로 1건    .venv/bin/python -m stock_auto.tools.alert_check --send")
    print("  형식 4종      .venv/bin/python -m stock_auto.tools.send_sample")
    return 0


def _summary_message(info: dict) -> str:
    """실제 누적 데이터로 만든 점검 메시지. 추천이 아니므로 오해 없게 적는다."""
    import socket
    from stock_auto.tracking import store, triggers as T
    L = [f"🔧 알림 점검 — {socket.gethostname().split('.')[0]}",
         "(추천이 아닙니다. 알림 경로가 살아 있는지 보는 메시지입니다)", ""]
    L.append(f"신호 기록 {info.get('signals', 0)}건 "
             f"(청산완료 {info.get('closed', 0)}건)")
    L.append(f"진입 트리거 {info.get('triggers', 0)}건 "
             f"(대기 {info.get('pending', 0)}건)")
    try:
        pend = [r for r in T.load() if r.get("status") == "pending"]
        for r in pend[:5]:
            L.append(f"  • {r.get('symbol')} {r.get('kind')} "
                     f"{r.get('value_pct')}% → {r.get('target_price')}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from stock_auto.tracking import usage_log
        line = usage_log.telegram_line()
        if line:
            L += ["", line]
    except Exception:  # noqa: BLE001
        pass
    try:
        from stock_auto.tracking.llm_eval import summary_verdict
        L += ["", summary_verdict(store.load())]
    except Exception:  # noqa: BLE001
        pass
    L += ["", "이 메시지가 보이면 텔레그램 경로는 정상입니다."]
    return "\n".join(L)


if __name__ == "__main__":
    raise SystemExit(main())
