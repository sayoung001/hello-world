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


def _format_hold(hold_rec) -> str:
    """hold_recommendation dict → 읽기 쉬운 문자열"""
    if not hold_rec or not isinstance(hold_rec, dict):
        return ""
    remaining = hold_rec.get("remaining_h", 0)
    optimal = hold_rec.get("optimal_hold_h", 0)
    urgency = hold_rec.get("urgency", "")
    current = hold_rec.get("current_hold_h", 0)
    if optimal == 0:
        return "즉시 청산 권고"
    parts = []
    if remaining > 0:
        parts.append(f"잔여 {remaining:.0f}h/{optimal:.0f}h")
    else:
        parts.append(f"초과 보유 ({current:.0f}h/{optimal:.0f}h)")
    if urgency == "high":
        parts.append("⚡긴급")
    return " ".join(parts)


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
                    f"홀딩: {_format_hold(v.get('hold_recommendation'))}\n"
                    f"  사유: {v.get('reasoning', '')[:100]}\n"
                )
            messages.append(pos_text)

        return messages

    def format_emergency(self, crash: dict, consensus: MarketConsensus) -> list[str]:
        """
        긴급 분석 결과 포맷.

        구성:
        1. 급락 상황 요약 (가격/ROE/청산거리)
        2. 급락 원인 분석 (뉴스/매크로 에이전트 결과)
        3. 종합 판단 (Bull/Bear 토론 결과 포함)
        4. 포지션별 홀드/청산 판결
        """
        messages = []
        level = crash.get("level", 0)
        icons = {1: "⚠️", 2: "🔴", 3: "🚨", 4: "💀"}
        icon = icons.get(level, "🔴")

        # ── 메시지 1: 상황 + 원인 + 종합 판단 ──

        lines = [
            f"{icon}{icon}{icon} 긴급 시장 분석 보고서 {icon}{icon}{icon}",
            f"{'━'*30}",
            f"⏰ {crash.get('timestamp', '')}",
            f"",
            f"📉 급락 상황",
            f"  BTC {crash.get('window', '')} 변동: {crash.get('change_pct', 0):+.2f}%",
            f"  현재가: ${crash.get('current_price', 0):,.0f}",
            f"  20x ROE 영향: {crash.get('roe_impact', 0):+.1f}%",
        ]

        remaining = crash.get("remaining_to_liq", 5.0)
        if remaining > 0:
            lines.append(f"  롱 청산까지: {remaining:.2f}%")
        else:
            lines.append(f"  💀 롱 진입가 기준 청산 구간 돌파")

        vol = crash.get("volume_spike", {})
        if vol.get("spike"):
            lines.append(f"  거래량: 평균 {vol['ratio']:.1f}배 급증 ⚡")

        # 멀티타임프레임
        multi_tf = crash.get("multi_tf", {})
        if multi_tf:
            lines.append(f"")
            lines.append(f"📊 멀티타임프레임:")
            for window, tf_data in multi_tf.items():
                lines.append(
                    f"  {window}: {tf_data['change_pct']:+.2f}% "
                    f"(ROE {tf_data['roe_impact']:+.0f}%)"
                )

        # ── 급락 원인 (에이전트 분석에서 추출) ──
        lines.append(f"")
        lines.append(f"{'─'*30}")
        lines.append(f"🔎 급락 원인 분석")
        lines.append(f"{'─'*30}")

        cause_found = False
        for msg in consensus.agent_messages:
            # 뉴스/이슈 에이전트 (Agent 6)
            if "뉴스" in msg.agent_name or "news" in msg.agent_id:
                breaking = msg.data.get("breaking_news", [])
                geo_risk = msg.data.get("geopolitical_risk", "")
                sentiment = msg.data.get("overall_sentiment", "")

                if breaking:
                    lines.append(f"")
                    lines.append(f"📰 주요 이슈:")
                    for news in breaking[:3]:
                        if isinstance(news, dict):
                            lines.append(f"  • {news.get('title', news.get('summary', ''))[:80]}")
                            impact = news.get("impact_score", 0)
                            if impact >= 7:
                                lines.append(f"    → 영향도: {impact}/10 (높음)")
                        else:
                            lines.append(f"  • {str(news)[:80]}")
                    cause_found = True

                if geo_risk and geo_risk not in ("low", ""):
                    lines.append(f"  🌐 지정학 리스크: {geo_risk.upper()}")
                    cause_found = True

                if sentiment:
                    lines.append(f"  💭 시장 감성: {sentiment}")

            # 매크로 에이전트 (Agent 2)
            if "매크로" in msg.agent_name or "macro" in msg.agent_id:
                events = msg.data.get("upcoming_events", [])
                risk_appetite = msg.data.get("risk_appetite", "")
                fear_greed = msg.data.get("fear_greed", {})

                if events:
                    lines.append(f"")
                    lines.append(f"📅 매크로 이벤트:")
                    for evt in events[:2]:
                        if isinstance(evt, dict):
                            lines.append(f"  • {evt.get('event', evt.get('name', ''))} (D{evt.get('days_until', '?')})")
                        else:
                            lines.append(f"  • {str(evt)[:60]}")
                    cause_found = True

                if risk_appetite:
                    lines.append(f"  📈 시장 성향: {risk_appetite}")
                if isinstance(fear_greed, dict) and fear_greed.get("value"):
                    lines.append(f"  😱 Fear & Greed: {fear_greed['value']} ({fear_greed.get('label', '')})")

            # BTC 구조 에이전트 (Agent 1)
            if "BTC" in msg.agent_name or "btc" in msg.agent_id:
                trend = msg.data.get("trend", "")
                support = msg.data.get("key_support", msg.data.get("support_levels", ""))
                if trend:
                    lines.append(f"")
                    lines.append(f"📐 BTC 기술적 분석:")
                    lines.append(f"  추세: {trend}")
                if support:
                    lines.append(f"  지지선: {support}")

            # 상관관계 에이전트 (Agent 3)
            if "상관" in msg.agent_name or "corr" in msg.agent_id:
                regime = msg.data.get("regime", "")
                if regime:
                    lines.append(f"  🔗 시장 동조: {regime}")

        if not cause_found:
            lines.append(f"  (뉴스/매크로 특이사항 미확인 — 기술적 매도 압력 추정)")

        # ── 종합 판단 ──
        lines.append(f"")
        lines.append(f"{'─'*30}")
        lines.append(f"🧠 AI 종합 판단")
        lines.append(f"{'─'*30}")
        lines.append(f"")
        lines.append(f"  리스크: {RISK_EMOJI.get(consensus.overall_risk, consensus.overall_risk.value)}")
        lines.append(f"  시장 상태: {consensus.market_regime.value}")
        lines.append(f"  BTC 편향: {consensus.btc_bias}")
        lines.append(f"  신뢰도: {consensus.confidence:.0%}")

        # Bull/Bear 토론 결과
        debate = getattr(consensus, 'debate', {})
        verdict = getattr(consensus, 'bull_bear_verdict', '')
        if debate:
            lines.append(f"")
            bull_arg = debate.get('bull', '')
            bear_arg = debate.get('bear', '')
            if bull_arg:
                lines.append(f"  🐂 Bull: {bull_arg[:120]}")
            if bear_arg:
                lines.append(f"  🐻 Bear: {bear_arg[:120]}")
            if verdict:
                verdict_label = {"bull_win": "강세론 우세", "bear_win": "약세론 우세", "draw": "팽팽"}
                lines.append(f"  → 토론 결과: {verdict_label.get(verdict, verdict)}")

        # 주요 경고
        if consensus.warnings:
            lines.append(f"")
            lines.append(f"  ⚠️ 핵심 경고:")
            for w in consensus.warnings[:4]:
                lines.append(f"    • {w}")

        messages.append("\n".join(lines))

        # ── 메시지 2: 포지션별 판결 (있을 경우) ──
        if consensus.position_verdicts:
            pos_lines = [
                f"",
                f"{'━'*30}",
                f"⚖️ 포지션별 판결 — 홀드 vs 청산",
                f"{'━'*30}",
            ]
            for v in consensus.position_verdicts:
                risk_str = v.get("risk_level", "N/A")
                action = v.get("action", "hold")
                action_str = ACTION_EMOJI.get(action, action)

                if risk_str == "DANGER" or action == "close":
                    pos_icon = "🔴"
                elif risk_str == "CAUTION" or action == "reduce_size":
                    pos_icon = "🟡"
                else:
                    pos_icon = "🟢"

                pos_lines.extend([
                    f"",
                    f"{pos_icon} {v.get('symbol', '?')} ({v.get('direction', '?')})",
                    f"  판결: {action_str}",
                    f"  리스크: {risk_str} | 신뢰도: {v.get('confidence', 0):.0%}",
                ])

                reasoning = v.get("reasoning", "")
                if reasoning:
                    pos_lines.append(f"  근거: {reasoning[:120]}")

                # SL/TP 조정 권고
                sl_tp = v.get("sl_tp_adjustment", {})
                if isinstance(sl_tp, dict):
                    sl_act = sl_tp.get("sl_action", "keep")
                    tp_act = sl_tp.get("tp_action", "keep")
                    sl_reason = sl_tp.get("reasoning", "")
                    if sl_act and sl_act != "keep":
                        pos_lines.append(f"  🔧 SL: {sl_act} — {sl_reason[:60]}")
                    if tp_act and tp_act != "keep":
                        pos_lines.append(f"  🔧 TP: {tp_act}")

                hold_rec = v.get("hold_recommendation", {})
                hold_str = _format_hold(hold_rec)
                if hold_str:
                    pos_lines.append(f"  ⏱️ 홀딩: {hold_str}")

                dissent = v.get("dissent", "")
                if dissent:
                    pos_lines.append(f"  💬 반대의견: {dissent[:80]}")

            pos_lines.append(f"{'━'*30}")
            messages.append("\n".join(pos_lines))
        else:
            messages.append(
                f"\n📭 보유 포지션 없음 — 급락 중 신규 진입 자제 권고"
            )

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
                    # 1차: HTML
                    resp = requests.post(url, json={
                        "chat_id": self.chat_id,
                        "text": chunk,
                        "parse_mode": "HTML",
                    }, timeout=10)
                    if resp.ok:
                        continue
                    # HTML 실패 → plain text 폴백
                    print(f"  [TG] HTML 실패({resp.status_code}), plain 재시도")
                    resp = requests.post(url, json={
                        "chat_id": self.chat_id,
                        "text": chunk,
                    }, timeout=10)
                    if not resp.ok:
                        print(f"❌ 텔레그램 plain도 실패: {resp.text[:100]}")
                        success = False
                except Exception as e:
                    print(f"❌ 텔레그램 전송 오류: {e}")
                    success = False
        return success
