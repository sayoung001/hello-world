"""
환경변수 로더 — .env(python-dotenv) → 설정 객체.

Google SSH(VM)의 .env에 키를 넣어두면 여기서 일괄 로드한다.
변수명은 .env.example과 일치.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_env(dotenv_path: str | None = None) -> None:
    """.env 로드(있으면). python-dotenv 미설치 시 OS 환경변수만 사용."""
    try:
        from dotenv import load_dotenv
        path = dotenv_path or str(Path.cwd() / ".env")
        load_dotenv(path)
    except ImportError:
        pass


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
