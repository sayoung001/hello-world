"""
telegram_formatter.py — 텔레그램 출력 포맷터
==============================================
MarketConsensus → 읽기 쉬운 텔레그램 메시지로 변환.
4000자 제한 대응 (분할 전송).
"""

from __future__ import annotations
import requests
from typing import Any

from agents.core.protocol import MarketConsensus, RiskLevel


RISK_EMOJI = {
    RiskLevel.SAFE: "🟢 SAFE",
    RiskLevel.CAUTION: "🟡 CAUTION",
    RiskLevel.DANGER: "🔴 DANGER",
}

ACTION_EMOJI = {
    "hold": "✅ 홀딩",
    "reduce_size": "⚠️ 축소",
    "close": "🔴 청산",
    "add": "➕ 추가",
    "adjust_sl": "🔧 SL 조정",
    "adjust_tp": "🔧 TP 조정",
}


class TelegramFormatter:
    """텔레그램 메시지 포맷터 + 전송"""

    def __init__(self, bot_token: str = "", chat_id: str = ""):
        self.bot_token = bot_token
        self.chat_id = chat_id

    def format_consensus(self, consensus: MarketConsensus) -> list[str]:
        """MarketConsensus를 텔레그램 메시지 문자열 리스트로 변환"""
        messages = []

        # === 헤더 + 종합 판단 ===
        header = (
            f"{'='*30}\n"
            f"🔍 멀티 에이전트 시장 분석\n"
            f"{'='*30}\n"
            f"⏰ {consensus.timestamp}\n\n"
            f"📊 종합 리스크: {RISK_EMOJI.get(consensus.overall_risk, consensus.overall_risk.value)}\n"
            f"🌍 시장 상태: {consensus.market_regime.value}\n"
            f"₿ BTC 편향: {consensus.btc_bias}\n"
            f"🎯 신뢰도: {consensus.confidence:.0%}\n"
        )

        # 경고
        if consensus.warnings:
            header += f"\n⚠️ 경고:\n"
            for w in consensus.warnings[:5]:
                header += f"  • {w}\n"

        messages.append(header)

        # === 에이전트별 분석 요약 ===
        if consensus.agent_messages:
            agent_text = f"\n{'─'*30}\n📋 에이전트별 분석\n{'─'*30}\n"
            for msg in consensus.agent_messages:
                agent_text += (
                    f"\n🤖 {msg.agent_name} (신뢰도: {msg.confidence:.0%})\n"
                    f"  {msg.reasoning[:150]}\n"
                )
                if msg.warnings:
                    for w in msg.warnings[:2]:
                        agent_text += f"  ⚠️ {w}\n"
            messages.append(agent_text)

        # === 포지션별 판결 ===
        if consensus.position_verdicts:
            pos_text = f"\n{'─'*30}\n⚖️ 포지션 판결\n{'─'*30}\n"
            for v in consensus.position_verdicts:
                risk_str = v.get("risk_level", "N/A")
                action_str = ACTION_EMOJI.get(v.get("action", ""), v.get("action", ""))
                pos_text += (
                    f"\n{'🟢' if risk_str == 'SAFE' else '🟡' if risk_str == 'CAUTION' else '🔴'} "
                    f"{v.get('symbol', '?')} ({v.get('direction', '?')})\n"
                    f"  리스크: {risk_str} | 액션: {action_str}\n"
                    f"  신뢰도: {v.get('confidence', 0):.0%} | "
                    f"홀딩: {v.get('hold_duration', 'N/A')}\n"
                    f"  사유: {v.get('reasoning', '')[:100]}\n"
                )
            messages.append(pos_text)

        return messages

    def send(self, consensus: MarketConsensus) -> bool:
        """텔레그램 전송 (4000자 분할)"""
        if not self.bot_token or not self.chat_id:
            print("⚠️ 텔레그램 설정 없음 — 콘솔 출력만 수행")
            for msg in self.format_consensus(consensus):
                print(msg)
            return False

        messages = self.format_consensus(consensus)
        success = True
        for msg in messages:
            # 4000자 분할
            chunks = [msg[i:i+4000] for i in range(0, len(msg), 4000)]
            for chunk in chunks:
                try:
                    url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
                    resp = requests.post(url, json={
                        "chat_id": self.chat_id,
                        "text": chunk,
                        "parse_mode": "HTML",
                    }, timeout=10)
                    if not resp.ok:
                        print(f"❌ 텔레그램 전송 실패: {resp.text[:100]}")
                        success = False
                except Exception as e:
                    print(f"❌ 텔레그램 전송 오류: {e}")
                    success = False
        return success
