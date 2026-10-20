"""
진입 트리거 점검 — pending 트리거를 일봉으로 판정해 발화·만료시킨다.

사용:
  .venv/bin/python -m stock_auto.pipeline.check_triggers              # 판정 + 알림
  .venv/bin/python -m stock_auto.pipeline.check_triggers --list       # 목록만(판정 안 함)
  .venv/bin/python -m stock_auto.pipeline.check_triggers --dry-run    # 판정하되 알림·게시 생략
  .venv/bin/python -m stock_auto.pipeline.check_triggers --setup-db   # Notion DB 1회 생성

매일 배치 뒤에 돌리면 "오늘 진입 조건에 닿은 종목"이 알림으로 온다.
장중 실시간 감시는 모니터(run_monitor)에 붙어 있어 닿는 순간 알린다.
이쪽은 장중에 놓친 것까지 당일 저가·고가로 잡아내는 사후점검이다.
"""

from __future__ import annotations

import argparse

from stock_auto.config.env import get_secrets
from stock_auto.tracking import triggers as T
from stock_auto.tracking import trigger_notify as N


def _fmt(r: dict) -> str:
    cond = N._condition_text(r).split("  (")[0]
    return (f"  {r.get('date')} {r.get('symbol'):<6} [{r.get('status')}] "
            f"{cond}\n"
            f"      기준 {r.get('basis_price')} → 목표 {r.get('target_price')} "
            f"· {N.category_of(r)} · "
            f"{N.HORIZON_BUCKET.get(str(r.get('horizon','')), '중기')}"
            + (f" · 도달 {r.get('fired_price')} ({r.get('fired_at')})"
               if r.get("status") == "fired" else ""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="진입 트리거 점검")
    ap.add_argument("--path", default=None, help="triggers.csv 경로")
    ap.add_argument("--list", action="store_true", help="목록만 출력")
    ap.add_argument("--dry-run", action="store_true",
                    help="판정하되 Notion 게시·Telegram 알림 생략")
    ap.add_argument("--setup-db", action="store_true",
                    help="Notion '진입 신호 도달' DB를 1회 생성하고 ID 출력")
    args = ap.parse_args(argv)

    sec = get_secrets()

    # ── DB 1회 셋업 ──
    if args.setup_db:
        from stock_auto.integrations.notion_publisher import NotionPublisher
        if not sec.has_notion or not sec.notion_parent_page_id:
            print("❌ NOTION_TOKEN / NOTION_PARENT_PAGE_ID 가 .env에 필요합니다.")
            return 1
        pub = NotionPublisher(token=sec.notion_token)
        # 있으면 재사용한다. 예전에는 돌릴 때마다 새 DB가 생겨 같은 제목의
        # DB가 3개까지 쌓였다 — 어느 ID가 .env 에 있는지 알 수 없게 된다.
        # .env 에 이미 적힌 ID 를 넘긴다 — 중복 중에 그게 있으면 그걸 쓴다
        ds_id, how = pub.ensure_triggers_db(sec.notion_parent_page_id,
                                            prefer_id=sec.notion_trigger_db_id)
        if not ds_id:
            print("❌ DB 생성 실패 — 위 [notion] 로그 확인")
            return 1
        cur = (sec.notion_trigger_db_id or "").strip()
        if how == "found":
            print(f"✅ 이미 있는 DB를 찾았습니다(새로 만들지 않음).")
        else:
            print(f"✅ 새로 생성했습니다.")
        if cur == ds_id:
            print(f"   .env 의 NOTION_TRIGGER_DB_ID 가 이미 이 값입니다 — 할 일 없음.")
        elif cur:
            print(f"   ⚠️ .env 의 NOTION_TRIGGER_DB_ID({cur[:8]}…)가 이 DB와 다릅니다.\n"
                  f"   아래 값으로 바꾸고 데몬을 재시작하세요:\n"
                  f"   NOTION_TRIGGER_DB_ID={ds_id}")
        else:
            print(f"   .env 에 아래 한 줄을 추가하고 데몬을 재시작하세요:\n"
                  f"   NOTION_TRIGGER_DB_ID={ds_id}")
        return 0

    rows = T.load(args.path)
    if not rows:
        print("등록된 트리거가 없습니다. 일일 배치를 먼저 돌리세요.")
        return 0

    # ── 목록만 ──
    if args.list:
        by_status: dict[str, list] = {}
        for r in rows:
            by_status.setdefault(r.get("status", "?"), []).append(r)
        for st in ("pending", "fired", "expired"):
            group = by_status.get(st, [])
            if not group:
                continue
            print(f"\n=== {st} {len(group)}건 ===")
            for r in group[-20:]:
                print(_fmt(r))
        # 조건 출처 분포 — LLM이 스키마를 제대로 채우는지 보는 지표
        src: dict[str, int] = {}
        for r in rows:
            src[r.get("source", "?")] = src.get(r.get("source", "?"), 0) + 1
        print(f"\n조건 출처: " + " · ".join(f"{k} {v}" for k, v in sorted(src.items())))
        print("  schema=LLM이 구조화해 반환 / parsed=문장에서 추출 / default=조건 없음")
        return 0

    # ── 판정 ──
    from stock_auto.tracking.trigger_eval import evaluate_daily, TriggerFirer
    import os

    if args.dry_run:
        firer = TriggerFirer(notion=None, telegram=_Silent(), send_chart=False)
        print("[dry-run] 판정만 하고 알림·게시는 하지 않습니다.")
    else:
        from stock_auto.integrations.notion_publisher import NotionPublisher
        notion = NotionPublisher(token=sec.notion_token) if sec.has_notion else None
        db_id = sec.notion_trigger_db_id or None   # get_secrets 가 .env 를 읽는다
        if notion and not db_id:
            print("[trigger] NOTION_TRIGGER_DB_ID 미설정 — 부모 페이지에 개별 페이지로 게시")
            print("          DB로 쌓으려면: --setup-db 로 1회 생성")
        firer = TriggerFirer(notion=notion, db_id=db_id,
                             parent_page=sec.notion_parent_page_id or None)

    stat = evaluate_daily(base=args.path, firer=firer, dry_run=args.dry_run)
    print(f"\n발화 {stat['fired']} · 만료 {stat['expired']} · "
          f"대기 {stat['pending']} · 데이터없음 {stat['nodata']}")
    if stat["fired"]:
        for r in T.load(args.path):
            if r.get("status") == "fired" and r.get("notify_at"):
                print(_fmt(r))
    return 0


class _Silent:
    """dry-run용 — enabled=False로 두면 콘솔 출력만 된다."""
    enabled = False


if __name__ == "__main__":
    raise SystemExit(main())
