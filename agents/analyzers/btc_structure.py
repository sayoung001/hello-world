"""
btc_structure.py — Agent 1: BTC 구조 분석가
=============================================
BTC 가격 구조, 핵심 레벨, 청산 클러스터 분석.
분석 유형: Factual (사실 기반)

데이터 소스:
- Binance API (가격, 거래량)
- CoinGlass (청산 히트맵, OI 변화)
- btc_filter.py (기존 BTC 상태 분석 재활용)
"""

from __future__ import annotations
import os
import ccxt
import pandas as pd
import numpy as np
from typing import Any

from agents.core.base import AgentBase
from agents.core.protocol import AgentMessage, RiskLevel


class BTCStructureAgent(AgentBase):
    """
    BTC 구조 분석가

    책임:
    - 주요 지지/저항 레벨 식별
    - 청산 클러스터 위치 파악 (20x 레버리지 감안)
    - 돌파 확률 추정
    - OI/펀딩레이트 기반 시장 과열 판단
    """

    def __init__(self, cg_client=None, exchange=None):
        super().__init__(
            agent_id="agent_1_btc",
            agent_name="BTC 구조 분석가",
            role_description="BTC 가격 구조와 핵심 레벨을 분석하는 Factual 에이전트"
        )
        self._analysis_type = "factual"
        self.cg = cg_client
        self.exchange = exchange or ccxt.binance({
            "enableRateLimit": True,
            "options": {"defaultType": "future"}
        })

    def _get_system_prompt(self) -> str:
        return """당신은 BTC 가격 구조 전문 분석가입니다.
20배 레버리지 선물 트레이딩 환경에서 BTC의 핵심 가격 레벨을 분석합니다.

분석 원칙:
- 사실적(Factual) 데이터에 기반한 객관적 분석
- 청산 클러스터 위치는 20x 레버리지 기준으로 평가
- 돌파 확률은 보수적으로 추정 (생존 우선)

반드시 JSON 형식으로 출력하세요:
```json
{
  "btc_price": 현재가,
  "key_resistance": [저항 레벨들],
  "key_support": [지지 레벨들],
  "breakout_probability": 상방돌파확률(0~1),
  "trend": "bullish|neutral|bearish",
  "oi_signal": "OI 분석 요약",
  "funding_signal": "펀딩레이트 분석 요약",
  "risk_level": "SAFE|CAUTION|DANGER",
  "reasoning": "분석 근거"
}
```"""

    def collect_data(self) -> dict:
        """BTC 관련 데이터 수집 (API 직접 호출)"""
        data = {}

        # 1. Binance OHLCV (15분봉, 1시간봉, 4시간봉)
        for tf in ["15m", "1h", "4h"]:
            try:
                ohlcv = self.exchange.fetch_ohlcv("BTC/USDT", timeframe=tf, limit=100)
                df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
                data[f"btc_{tf}"] = {
                    "current": float(df["close"].iloc[-1]),
                    "high_24h": float(df["high"].tail(96 if tf == "15m" else 24 if tf == "1h" else 6).max()),
                    "low_24h": float(df["low"].tail(96 if tf == "15m" else 24 if tf == "1h" else 6).min()),
                    "volume_avg": float(df["volume"].tail(20).mean()),
                    "volume_latest": float(df["volume"].iloc[-1]),
                    # EMA 계산
                    "ema_12": float(df["close"].ewm(span=12).mean().iloc[-1]),
                    "ema_26": float(df["close"].ewm(span=26).mean().iloc[-1]),
                }
            except Exception as e:
                data[f"btc_{tf}"] = {"error": str(e)}

        # 2. CoinGlass 데이터 (있으면)
        if self.cg:
            try:
                oi = self.cg.get_oi_change("BTC")
                data["oi"] = oi or {}
            except Exception:
                data["oi"] = {}

            try:
                funding = self.cg.get_funding_rate_trend("BTC")
                data["funding"] = funding or {}
            except Exception:
                data["funding"] = {}

            try:
                ls = self.cg.get_global_ls_ratio("BTC")
                data["ls_ratio"] = ls or {}
            except Exception:
                data["ls_ratio"] = {}

        return data

    def analyze(self, collected_data: dict, context: dict | None = None) -> AgentMessage:
        """BTC 구조 분석 수행"""
        # 데이터 요약 생성
        btc_4h = collected_data.get("btc_4h", {})
        btc_1h = collected_data.get("btc_1h", {})
        oi = collected_data.get("oi", {})
        funding = collected_data.get("funding", {})

        prompt = f"""다음 BTC 데이터를 분석하여 구조적 판단을 내려주세요.

## 가격 데이터
- 현재가: ${btc_4h.get('current', 'N/A')}
- 24h 고가: ${btc_4h.get('high_24h', 'N/A')}
- 24h 저가: ${btc_4h.get('low_24h', 'N/A')}
- EMA 12: ${btc_4h.get('ema_12', 'N/A')}
- EMA 26: ${btc_4h.get('ema_26', 'N/A')}
- 거래량 (최근/평균): {btc_4h.get('volume_latest', 'N/A')} / {btc_4h.get('volume_avg', 'N/A')}

## 파생상품 데이터
- OI 변화: {oi.get('change_pct', 'N/A')}%, 트렌드: {oi.get('trend', 'N/A')}
- 펀딩레이트: {funding.get('rates_str', 'N/A')}, 시그널: {funding.get('signal', 'N/A')}

## 20x 레버리지 핵심 고려사항
- ~4% 역행 = 청산
- 청산 클러스터가 가격 자석 역할 가능

주요 지지/저항 레벨을 식별하고 상방 돌파 확률을 추정하세요."""

        result = self.llm_json(prompt, deep=True)

        # 파싱 실패 시 기본값
        if result.get("parse_error"):
            result = self._fallback_analysis(collected_data)

        confidence = result.get("confidence", 0.6)
        warnings = []
        if result.get("risk_level") == "DANGER":
            warnings.append("⚠️ BTC 구조적 위험 감지")

        return self._build_message(
            data=result,
            confidence=confidence,
            reasoning=result.get("reasoning", ""),
            warnings=warnings
        )

    def _fallback_analysis(self, data: dict) -> dict:
        """LLM 파싱 실패 시 규칙 기반 폴백"""
        btc_4h = data.get("btc_4h", {})
        price = btc_4h.get("current", 0)
        ema12 = btc_4h.get("ema_12", 0)
        ema26 = btc_4h.get("ema_26", 0)

        if ema12 > ema26:
            trend = "bullish"
            breakout_prob = 0.55
        elif ema12 < ema26:
            trend = "bearish"
            breakout_prob = 0.35
        else:
            trend = "neutral"
            breakout_prob = 0.45

        return {
            "btc_price": price,
            "key_resistance": [],
            "key_support": [],
            "breakout_probability": breakout_prob,
            "trend": trend,
            "risk_level": "CAUTION",
            "reasoning": "규칙 기반 폴백 분석 (LLM 파싱 실패)"
        }
