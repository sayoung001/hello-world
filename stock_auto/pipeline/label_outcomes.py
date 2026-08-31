"""
결과 라벨링 + 성과/캘리브레이션 리포트 출력.

사용:
  python -m stock_auto.pipeline.label_outcomes                 # 라벨링 + 콘솔 출력
  python -m stock_auto.pipeline.label_outcomes --out report.md # 파일로 저장
  python -m stock_auto.pipeline.label_outcomes --notion        # Notion 게시
  python -m stock_auto.pipeline.label_outcomes --export-csv cal.csv  # 캘리브레이션 데이터셋

매일 배치 후(또는 주 1회) 실행하면 누적 신호에 결과가 붙고
점수→승률 변환표가 갱신된다. 이 표가 확률층(캘리브레이션)의 입력이다.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from stock_auto.tracking import store, labeler, report as report_mod


def main() -> int:
    ap = argparse.ArgumentParser(description="신호 결과 라벨링 및 리포트")
    ap.add_argument("--path", default=store.DEFAULT_PATH, help="신호 CSV 경로")
    ap.add_argument("--out", help="리포트 저장 경로(.md)")
    ap.add_argument("--export-csv", help="캘리브레이션 학습용 CSV 경로")
    ap.add_argument("--notion", action="store_true", help="Notion에 리포트 게시")
    ap.add_argument("--telegram", action="store_true", help="요약을 텔레그램 전송")
    ap.add_argument("--no-label", action="store_true", help="라벨링 없이 리포트만")
    args = ap.parse_args()

    if not args.no_label:
        stat = labeler.label_pending(args.path)
        print(f"[label] 신규 라벨 {stat['labeled']}건 · "
              f"결과 대기 {stat['pending']}건 · 데이터없음 {stat['nodata']}건")

    rows = store.load(args.path)
    md = report_mod.build_report(rows)
    print("\n" + md)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"\n[report] 저장 → {args.out}")

    if args.export_csv:
        ds = report_mod.calibration_dataset(rows)
        if ds:
            p = Path(args.export_csv)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(ds[0].keys()))
                w.writeheader()
                w.writerows(ds)
            print(f"[export] 캘리브레이션 데이터 {len(ds)}행 → {args.export_csv}")
        else:
            print("[export] TP/SL 라벨이 아직 없어 데이터셋을 만들 수 없습니다")

    if args.notion:
        from stock_auto.config.env import get_secrets
        from stock_auto.integrations.notion_publisher import NotionPublisher
        sec = get_secrets()
        if sec.has_notion and sec.notion_parent_page_id:
            pub = NotionPublisher(token=sec.notion_token)
            from datetime import datetime
            title = f"성과 리포트 {datetime.now():%Y-%m-%d}"
            pid = pub.create_page_from_markdown(
                sec.notion_parent_page_id, title, md)
            print(f"[notion] {'게시 완료 ' + str(pid) if pid else '게시 실패'}")
        else:
            print("[notion] NOTION_TOKEN / NOTION_PARENT_PAGE_ID 필요")

    if args.telegram:
        from stock_auto.config.env import get_secrets
        from stock_auto.notify.telegram import Telegram
        sec = get_secrets()
        tg = Telegram(sec.telegram_bot_token, sec.telegram_chat_id)
        head = "\n".join(md.split("\n")[:14])
        tg.send_message(head, parse_mode="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
