"""
배포 전 점검 (D) — VM 첫 실행 전 키·라이브러리·연결 스모크 테스트.

사용:
  python -m stock_auto.tools.preflight            # 키·라이브러리만(네트워크 X)
  python -m stock_auto.tools.preflight --live      # 외부 연결까지 점검

점검:
  1) .env 시크릿 존재(마스킹 출력)
  2) 필수 라이브러리 import
  3) --live: FDR 일봉 / KIS approval_key / Anthropic / Notion / Telegram 연결
"""

from __future__ import annotations

import argparse

from stock_auto.config.env import get_secrets

OK, NO, WARN = "✅", "❌", "⚠️"


def _mask(v: str) -> str:
    if not v:
        return "(미설정)"
    return v[:4] + "…" + v[-2:] if len(v) > 8 else "설정됨"


def check_secrets(sec) -> bool:
    print("── 시크릿 점검 ──")
    items = [
        ("KIS_APP_KEY", sec.kis_app_key), ("KIS_APP_SECRET", sec.kis_app_secret),
        ("KIS_ACCOUNT_NO", sec.kis_account_no),
        ("ANTHROPIC_API_KEY", sec.anthropic_api_key),
        ("NOTION_TOKEN", sec.notion_token),
        ("NOTION_PARENT_PAGE_ID", sec.notion_parent_page_id),
        ("TELEGRAM_BOT_TOKEN", sec.telegram_bot_token),
        ("TELEGRAM_CHAT_ID", sec.telegram_chat_id),
    ]
    for name, val in items:
        mark = OK if val else WARN
        print(f"  {mark} {name}: {_mask(val)}")
    print(f"  → KIS={sec.has_kis} Anthropic={sec.has_anthropic} "
          f"Notion={sec.has_notion} Telegram={sec.has_telegram}")
    return True


def check_libs() -> bool:
    print("\n── 라이브러리 점검 ──")
    libs = ["pandas", "numpy", "requests", "dotenv", "FinanceDataReader",
            "anthropic", "websocket", "apscheduler"]
    all_ok = True
    for lib in libs:
        try:
            __import__(lib)
            print(f"  {OK} {lib}")
        except ImportError:
            print(f"  {NO} {lib} (pip install -r requirements.txt)")
            all_ok = False
    return all_ok


def check_live(sec) -> bool:
    print("\n── 라이브 연결 점검(--live) ──")
    ok = True
    # FDR 일봉
    try:
        import FinanceDataReader as fdr
        from datetime import datetime, timedelta
        start = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        df = fdr.DataReader("AAPL", start)
        print(f"  {OK if len(df) else WARN} FDR 일봉 AAPL: {len(df)}행")
    except Exception as e:  # noqa: BLE001
        print(f"  {NO} FDR: {type(e).__name__}: {e}"); ok = False
    # KIS approval_key
    if sec.has_kis:
        from stock_auto.data.kis_feed import get_approval_key
        key = get_approval_key(sec.kis_app_key, sec.kis_app_secret, sec.kis_env)
        print(f"  {OK if key else NO} KIS approval_key: {'발급OK' if key else '실패'}")
        ok = ok and bool(key)
    # Anthropic
    if sec.has_anthropic:
        try:
            import anthropic
            c = anthropic.Anthropic(api_key=sec.anthropic_api_key)
            r = c.messages.create(model=sec.stock_agent_model, max_tokens=8,
                                  messages=[{"role": "user", "content": "ping"}])
            print(f"  {OK} Anthropic {sec.stock_agent_model}: 응답 OK")
        except Exception as e:  # noqa: BLE001
            print(f"  {NO} Anthropic: {type(e).__name__}: {e}"); ok = False
    # Notion
    if sec.has_notion:
        try:
            import requests
            r = requests.get("https://api.notion.com/v1/users/me",
                             headers={"Authorization": f"Bearer {sec.notion_token}",
                                      "Notion-Version": "2022-06-28"}, timeout=10)
            print(f"  {OK if r.ok else NO} Notion users/me: {r.status_code}")
            ok = ok and r.ok
        except Exception as e:  # noqa: BLE001
            print(f"  {NO} Notion: {type(e).__name__}: {e}"); ok = False
    # Telegram
    if sec.has_telegram:
        try:
            import requests
            r = requests.get(
                f"https://api.telegram.org/bot{sec.telegram_bot_token}/getMe",
                timeout=10)
            print(f"  {OK if r.ok else NO} Telegram getMe: {r.status_code}")
            ok = ok and r.ok
        except Exception as e:  # noqa: BLE001
            print(f"  {NO} Telegram: {type(e).__name__}: {e}"); ok = False
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="배포 전 점검")
    ap.add_argument("--live", action="store_true", help="외부 연결까지 점검")
    args = ap.parse_args()

    sec = get_secrets()
    print("=" * 50 + "\n  주식 자동매매 — 배포 전 점검\n" + "=" * 50)
    check_secrets(sec)
    libs_ok = check_libs()
    live_ok = check_live(sec) if args.live else True

    print("\n" + "=" * 50)
    if libs_ok and live_ok:
        print(f"{OK} 점검 통과 — 실행 가능")
        print("  다음: python -m stock_auto.pipeline.build_profiles --market KR")
        print("        python -m stock_auto.pipeline.run_daily_batch --market US --no-llm")
        return 0
    print(f"{WARN} 일부 항목 미충족 — 위 ❌ 확인 후 재점검")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
