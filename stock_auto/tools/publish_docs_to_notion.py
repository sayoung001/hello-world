"""
설계 문서 → Notion 게시 (A).

사용:
  python -m stock_auto.tools.publish_docs_to_notion [파일...]
  인자 없으면 기본 설계 문서를 게시.

필요 .env: NOTION_TOKEN, NOTION_PARENT_PAGE_ID
부모 페이지에 봇이 'connection'으로 공유돼 있어야 함(Notion 권한).
"""

from __future__ import annotations

import sys
from pathlib import Path

from stock_auto.config.env import get_secrets
from stock_auto.integrations.notion_publisher import NotionPublisher

DEFAULT_DOCS = [
    "주식자동매매_통합설계_및_검토.md",
    "US_KR_CLAUD_분석_및_발전방향.md",
]


def main(argv: list[str]) -> int:
    sec = get_secrets()
    if not sec.has_notion or not sec.notion_parent_page_id:
        print("❌ NOTION_TOKEN / NOTION_PARENT_PAGE_ID 가 .env에 필요합니다.")
        return 1

    pub = NotionPublisher(token=sec.notion_token)
    docs = argv or DEFAULT_DOCS
    ok = 0
    for doc in docs:
        p = Path(doc)
        if not p.exists():
            print(f"⚠️  파일 없음: {doc}")
            continue
        title = p.stem
        text = p.read_text(encoding="utf-8")
        print(f"📤 게시: {title} ({len(text)}자)")
        page_id = pub.create_page_from_markdown(
            sec.notion_parent_page_id, title, text)
        if page_id:
            print(f"   ✅ 생성됨: {page_id}")
            ok += 1
        else:
            print("   ❌ 실패")
    print(f"\n완료: {ok}/{len(docs)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
