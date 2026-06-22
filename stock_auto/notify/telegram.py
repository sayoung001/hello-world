"""
텔레그램 알림 — 실시간 모니터용 (사용자 정책: 실시간=Telegram, 배치=Notion).

코인봇 텔레그램 패턴 계승. 토큰/챗ID는 환경변수.
거래량 폭주(volume_monitor) 알림을 여기로 흘려보낸다.
"""

from __future__ import annotations

import os
from typing import Optional

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


class Telegram:
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        if not self.enabled:
            print(f"[telegram] 미설정 — 메시지 생략:\n{text}")
            return False
        try:
            import requests
            url = TELEGRAM_API.format(token=self.token, method="sendMessage")
            r = requests.post(url, json={
                "chat_id": self.chat_id, "text": text,
                "parse_mode": parse_mode, "disable_web_page_preview": True,
            }, timeout=10)
            r.raise_for_status()
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[telegram] 전송 실패: {type(e).__name__}: {e}")
            return False

    def send_document(self, path: str, caption: str = "") -> bool:
        if not self.enabled:
            print(f"[telegram] 미설정 — 파일 생략: {path}")
            return False
        try:
            import requests
            url = TELEGRAM_API.format(token=self.token, method="sendDocument")
            with open(path, "rb") as f:
                r = requests.post(url, data={"chat_id": self.chat_id, "caption": caption},
                                  files={"document": f}, timeout=30)
            r.raise_for_status()
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[telegram] 파일 전송 실패: {type(e).__name__}: {e}")
            return False


def surge_alert_callback(tg: Optional[Telegram] = None):
    """volume_monitor.SurgeMonitor(on_signal=...)에 꽂을 콜백 생성."""
    tg = tg or Telegram()
    def _cb(signal):
        tg.send_message(signal.to_telegram())
    return _cb
