"""
텔레그램 알림 — 실시간 모니터용 (사용자 정책: 실시간=Telegram, 배치=Notion).

코인봇 텔레그램 패턴 계승. 토큰/챗ID는 환경변수.
거래량 폭주(volume_monitor) 알림을 여기로 흘려보낸다.
"""

from __future__ import annotations

import os
from typing import Optional

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

# 텔레그램 메시지 상한은 4096자. 분할 표기("(1/3)")가 붙으므로 여유를 둔다.
MAX_MESSAGE_LEN = 3900


def chunk_text(text: str, limit: int = MAX_MESSAGE_LEN) -> list[str]:
    """줄 경계를 지키며 상한 이하로 분할. 한 줄이 상한을 넘으면 그 줄만 강제로 자른다."""
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    buf: list[str] = []
    size = 0
    for line in text.split("\n"):
        while len(line) > limit:               # 비정상적으로 긴 한 줄
            if buf:
                out.append("\n".join(buf)); buf, size = [], 0
            out.append(line[:limit])
            line = line[limit:]
        if size + len(line) + 1 > limit and buf:
            out.append("\n".join(buf)); buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        out.append("\n".join(buf))
    return out


class Telegram:
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        """
        메시지 전송. 텔레그램 상한(4096자)을 넘으면 줄 단위로 나눠 순서대로 보낸다.

        상한을 넘기면 API가 400을 돌려주고 **메시지 전체가 사라진다.**
        일일 다이제스트는 추천 건수에 따라 쉽게 4096자를 넘으므로 분할이 필수다.
        """
        if not self.enabled:
            print(f"[telegram] 미설정 — 메시지 생략:\n{text}")
            return False
        chunks = chunk_text(text)
        ok = True
        for i, ch in enumerate(chunks):
            suffix = f"\n\n({i + 1}/{len(chunks)})" if len(chunks) > 1 else ""
            ok = self._send_one(ch + suffix, parse_mode) and ok
        return ok

    def _send_one(self, text: str, parse_mode: str) -> bool:
        try:
            import requests
            url = TELEGRAM_API.format(token=self.token, method="sendMessage")
            payload = {"chat_id": self.chat_id, "text": text,
                       "disable_web_page_preview": True}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            r = requests.post(url, json=payload, timeout=10)
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
