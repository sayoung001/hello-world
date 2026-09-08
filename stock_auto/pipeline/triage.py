"""
트리아지 — 추천을 실제로 실행했는지 사람이 입력하는 경로.

왜 필요한가:
  `SignalRecord.executed`는 지금까지 아무도 채우지 않아 영구히 빈 값이었고,
  성과 리포트 5장 "실행 여부별(선택 편향 점검)"이 영원히 출력되지 않았다.
  반자동 시스템에서 **사람의 개입이 알파를 더하는지 빼는지를 재는 유일한 수단**이
  이 컬럼이다. 그리고 **기록 누락은 소급 복구가 되지 않는다** —
  오늘 입력하지 않은 날은 영영 미상으로 남는다.

사용:
  python -m stock_auto.pipeline.triage                 # 미분류 건을 하나씩 대화형 입력
  python -m stock_auto.pipeline.triage --list          # 미분류 목록만 출력(입력 안 함)
  python -m stock_auto.pipeline.triage --set a1b2c3:yes --set d4e5f6:no
  python -m stock_auto.pipeline.triage --all-no        # 남은 전부를 '미실행'으로 일괄 처리
  python -m stock_auto.pipeline.triage --telegram      # 텔레그램으로 미분류 목록 발송

입력값:
  yes  = 실제로 샀다
  no   = 보고도 사지 않았다      ← 이것도 반드시 기록해야 비교가 성립한다
  skip = 판단 보류(나중에 다시)  ← 미분류로 남기지 않고 명시적으로 미룬 것
"""

from __future__ import annotations

import argparse
import sys

from stock_auto.tracking import store

_HELP = """
  [y] 샀다   [n] 안 샀다   [s] 보류   [q] 그만두기(여기까지 저장)
  메모를 함께 남기려면  y 눌림목에서 못 삼   처럼 뒤에 이어 쓰세요.
""".rstrip()


def _fmt(r: dict) -> str:
    eff = r.get("effective_score", "")
    de = r.get("days_to_earnings", "")
    earn = f" · 실적 D-{de}" if str(de).strip() not in ("", "None") else ""
    reco = r.get("recommend") or "-"
    hz = r.get("horizon") or "-"
    return (f"{r.get('date')} {r.get('symbol'):<6} [{reco}/{hz}] "
            f"Eff {eff} · 종가 {r.get('close')} · 손절 {r.get('stop')} "
            f"· 목표 {r.get('target')}{earn}\n"
            f"      진입: {(r.get('entry_rule') or '-')[:80]}\n"
            f"      id={r.get('signal_id')}")


def _interactive(rows: list[dict], path: str) -> int:
    print(f"\n미분류 추천 {len(rows)}건. {_HELP}\n")
    updates: dict[str, tuple[str, str]] = {}
    key = {"y": "yes", "n": "no", "s": "skip"}
    for i, r in enumerate(rows, 1):
        print(f"[{i}/{len(rows)}] {_fmt(r)}")
        while True:
            try:
                raw = input("  > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n중단 — 여기까지 저장합니다.")
                raw = "q"
            if not raw:
                continue
            head, _, note = raw.partition(" ")
            head = head.lower()
            if head == "q":
                return _commit(updates, path)
            if head in key:
                updates[r["signal_id"]] = (key[head], note.strip())
                break
            print(f"  ? 알 수 없는 입력: {raw!r}{_HELP}")
    return _commit(updates, path)


def _commit(updates: dict[str, tuple[str, str]], path: str) -> int:
    if not updates:
        print("입력된 내용이 없습니다.")
        return 0
    n = store.set_executed_many(updates, path)
    yes = sum(1 for v in updates.values() if v[0] == "yes")
    print(f"\n저장 완료 — {n}건 (실행 {yes} / 미실행 {n - yes})")
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="추천 실행 여부 트리아지")
    ap.add_argument("--path", default=None,
                    help="신호 CSV 경로(기본: $STOCK_DATA_DIR/tracking/signals.csv)")
    ap.add_argument("--list", action="store_true", help="목록만 출력")
    ap.add_argument("--set", action="append", default=[], metavar="ID:VALUE",
                    help="비대화형 입력. 예: --set a1b2c3:yes")
    ap.add_argument("--all-no", action="store_true",
                    help="남은 미분류를 전부 '미실행(no)'으로 처리")
    ap.add_argument("--telegram", action="store_true",
                    help="미분류 목록을 텔레그램으로 발송")
    ap.add_argument("--include-gated", action="store_true",
                    help="게이트에 막힌 신호도 대상에 포함(기본 제외)")
    args = ap.parse_args(argv)

    rows = store.untriaged(args.path, include_gated=args.include_gated)

    # 비대화형 지정
    if args.set:
        updates: dict[str, tuple[str, str]] = {}
        for item in args.set:
            sid, _, val = item.partition(":")
            val = val.strip().lower()
            if val not in store.EXECUTED_VALUES:
                print(f"❌ 잘못된 값: {item!r} "
                      f"(허용: {'/'.join(store.EXECUTED_VALUES)})")
                return 1
            updates[sid.strip()] = (val, "")
        n = store.set_executed_many(updates, args.path)
        print(f"저장 완료 — {n}건" + ("" if n == len(updates) else
              f" (일치하지 않은 id {len(updates) - n}건)"))
        return 0

    if not rows:
        print("미분류 추천이 없습니다. 👍")
        return 0

    if args.telegram:
        return _send_telegram(rows)

    if args.list:
        print(f"미분류 추천 {len(rows)}건\n")
        for r in rows:
            print(_fmt(r) + "\n")
        print("입력하려면 인자 없이 다시 실행하거나 --set ID:yes 를 쓰세요.")
        return 0

    if args.all_no:
        n = store.set_executed_many(
            {r["signal_id"]: ("no", "일괄 미실행") for r in rows}, args.path)
        print(f"{n}건을 '미실행'으로 처리했습니다.")
        return 0

    if not sys.stdin.isatty():
        print(f"미분류 {len(rows)}건이 있으나 대화형 입력이 불가한 환경입니다.\n"
              "  --list 로 확인하고 --set ID:yes 로 입력하거나,\n"
              "  --telegram 으로 목록을 받아 처리하세요.")
        return 1

    return 0 if _interactive(rows, args.path) >= 0 else 1


def _send_telegram(rows: list[dict]) -> int:
    from stock_auto.config.env import get_secrets
    from stock_auto.notify.telegram import Telegram
    sec = get_secrets()
    tg = Telegram(sec.telegram_bot_token, sec.telegram_chat_id)
    lines = [f"📋 실행 여부 미입력 {len(rows)}건", ""]
    for r in rows[:20]:
        lines.append(f"• {r.get('date')} {r.get('symbol')} "
                     f"[{r.get('recommend') or '-'}] Eff {r.get('effective_score')} "
                     f"— `{r.get('signal_id')}`")
    if len(rows) > 20:
        lines.append(f"… 외 {len(rows) - 20}건")
    lines += ["", "입력: `python -m stock_auto.pipeline.triage --set <id>:yes`"]
    msg = "\n".join(lines)
    if tg.enabled:
        tg.send_message(msg)
        print(f"텔레그램 발송 완료 ({len(rows)}건)")
    else:
        print("텔레그램 미설정 — 콘솔 출력\n")
        print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
