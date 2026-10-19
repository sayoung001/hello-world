"""
결과 라벨링 + 성과/캘리브레이션 리포트 출력.

사용:
  .venv/bin/python -m stock_auto.pipeline.label_outcomes                 # 라벨링 + 콘솔 출력
  .venv/bin/python -m stock_auto.pipeline.label_outcomes --out report.md # 파일로 저장
  .venv/bin/python -m stock_auto.pipeline.label_outcomes --notion        # Notion 게시
  .venv/bin/python -m stock_auto.pipeline.label_outcomes --export-csv cal.csv  # 캘리브레이션 데이터셋
  .venv/bin/python -m stock_auto.pipeline.label_outcomes --llm-only --no-label  # LLM 계층 평가만

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
    ap.add_argument("--path", default=None,
                    help="신호 CSV 경로(기본: $STOCK_DATA_DIR/tracking/signals.csv)")
    ap.add_argument("--out", help="리포트 저장 경로(.md)")
    ap.add_argument("--export-csv", help="캘리브레이션 학습용 CSV 경로")
    ap.add_argument("--notion", action="store_true", help="Notion에 리포트 게시")
    ap.add_argument("--telegram", action="store_true", help="요약을 텔레그램 전송")
    ap.add_argument("--no-label", action="store_true", help="라벨링 없이 리포트만")
    ap.add_argument("--llm-only", action="store_true",
                    help="LLM 계층 평가만 출력(라벨링·대시보드 생략)")
    args = ap.parse_args()

    if not args.no_label:
        stat = labeler.label_pending(args.path)
        print(f"[label] 신규 라벨 {stat['labeled']}건 · "
              f"결과 대기 {stat['pending']}건 · 데이터없음 {stat['nodata']}건")

    # 보유일별 수익 곡선(forward_returns.csv) 갱신 — 라벨링과 별개로 매번
    fwd_rows: list = []
    if not args.no_label:
        try:
            from stock_auto.tracking import forward
            fs = forward.update(path=None if args.path is None else
                                str(Path(args.path).with_name("forward_returns.csv")))
            print(f"[forward] 갱신 {fs['updated']}건 · 20일 완료 {fs['complete']}건 · "
                  f"진입 대기 {fs['pending']}건")
        except Exception as e:  # noqa: BLE001
            print(f"[forward] 실패: {type(e).__name__}: {e}")

    rows = store.load(args.path)
    from stock_auto.tracking import forward, pnl
    fwd_rows = forward.load(None if args.path is None else
                            str(Path(args.path).with_name("forward_returns.csv")))
    if args.llm_only:
        from stock_auto.tracking import llm_eval
        print("\n" + llm_eval.build_llm_report(rows))
        return 0

    md = report_mod.build_report(rows)
    md += "\n" + "\n".join(forward.section_md(fwd_rows)) + "\n" + "\n".join(pnl.section_md(rows))
    # LLM 계층 평가 — 규칙 시그널 성과와 분리해서 붙인다. 둘을 섞으면
    # "점수가 좋아서 벌었나, LLM이 잘해서 벌었나"를 구분할 수 없다.
    try:
        from stock_auto.tracking import llm_eval
        md += "\n\n" + llm_eval.build_llm_report(rows)
    except Exception as e:  # noqa: BLE001 — 평가 실패가 리포트를 막지 않게
        md += f"\n\n[llm_eval] 실패: {type(e).__name__}: {e}"
    try:
        from stock_auto.tracking import usage_log
        md += "\n" + "\n".join(usage_log.section_md())
    except Exception as e:  # noqa: BLE001
        md += f"\n[usage] 실패: {type(e).__name__}: {e}"
    print("\n" + md)

    # 대시보드 HTML도 같이 갱신
    try:
        from stock_auto.tracking import dashboard
        dp = dashboard.write(rows=rows, fwd_rows=fwd_rows)
        print(f"[dashboard] 저장 → {dp}")
    except Exception as e:  # noqa: BLE001
        print(f"[dashboard] 실패: {type(e).__name__}: {e}")

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
        tg.send_message(pnl.telegram_summary(rows, fwd_rows), parse_mode="")
        try:
            tg.send_document(str(Path("logs/dashboard.html")),
                             caption="추천 성과 대시보드 (브라우저로 열기)")
        except Exception as e:  # noqa: BLE001
            print(f"[telegram] 대시보드 전송 실패: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
