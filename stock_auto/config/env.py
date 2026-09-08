"""
환경변수 로더 — .env(python-dotenv) → 설정 객체.

Google SSH(VM)의 .env에 키를 넣어두면 여기서 일괄 로드한다.
변수명은 .env.example과 일치.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_WARNED = {"dotenv": False}


def load_env(dotenv_path: str | None = None) -> None:
    """
    .env 로드. python-dotenv 미설치 시 OS 환경변수만 사용한다.

    ★ 조용히 넘어가면 안 되는 경우가 있다: **.env 파일은 있는데 python-dotenv가
      설치되지 않은 상태.** 이때 모든 키가 빈 값이 되어 텔레그램·LLM·KIS가 전부
      '미설정'으로 동작하는데, 배치는 정상 종료한다. 설치를 빠뜨린 첫 배포에서
      가장 흔하고 가장 헷갈리는 실패다 → 한 번은 반드시 경고한다.
    """
    path = Path(dotenv_path or (Path.cwd() / ".env"))
    try:
        from dotenv import load_dotenv
    except ImportError:
        if path.exists() and not _WARNED["dotenv"]:
            _WARNED["dotenv"] = True
            print(f"⚠️  {path} 파일은 있는데 python-dotenv가 설치되지 않았습니다.\n"
                  "    → .env가 무시되어 모든 키가 '미설정'으로 동작합니다.\n"
                  "    → 해결: pip install python-dotenv")
        return
    load_dotenv(str(path))


@dataclass(frozen=True)
class Secrets:
    # KIS
    kis_app_key: str
    kis_app_secret: str
    kis_account_no: str
    kis_account_product_code: str
    kis_env: str
    # Anthropic
    anthropic_api_key: str
    stock_agent_model: str
    # Notion
    notion_token: str
    notion_parent_page_id: str
    notion_reco_db_id: str
    # Telegram
    telegram_bot_token: str
    telegram_chat_id: str

    @property
    def has_kis(self) -> bool:
        return bool(self.kis_app_key and self.kis_app_secret)

    @property
    def has_anthropic(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def has_notion(self) -> bool:
        return bool(self.notion_token)

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


def get_secrets(load: bool = True) -> Secrets:
    if load:
        load_env()
    g = os.environ.get
    return Secrets(
        kis_app_key=g("KIS_APP_KEY", ""),
        kis_app_secret=g("KIS_APP_SECRET", ""),
        kis_account_no=g("KIS_ACCOUNT_NO", ""),
        kis_account_product_code=g("KIS_ACCOUNT_PRODUCT_CODE", "01"),
        kis_env=g("KIS_ENV", "real"),
        anthropic_api_key=g("ANTHROPIC_API_KEY", ""),
        stock_agent_model=g("STOCK_AGENT_MODEL", "claude-opus-4-8"),
        notion_token=g("NOTION_TOKEN", ""),
        notion_parent_page_id=g("NOTION_PARENT_PAGE_ID", ""),
        notion_reco_db_id=g("NOTION_RECO_DB_ID", ""),
        telegram_bot_token=g("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=g("TELEGRAM_CHAT_ID", ""),
    )
