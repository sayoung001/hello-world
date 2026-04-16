"""
news_sentiment.py — Agent 6: 뉴스/이슈 분석가
===============================================
실시간 뉴스 및 지정학적 이슈가 크립토에 미치는 영향 분석.
분석 유형: Subjective (주관적 해석)

핵심 기능:
- CryptoCompare / Alternative.me 뉴스 수집
- 지정학 이슈 감지 (전쟁, 제재, 규제)
- 뉴스 감성 분석 (LLM 기반)
- Breaking News 임팩트 스코어링

데이터 소스:
- CryptoCompare News API (무료)
- Alternative.me (공포탐욕 + 뉴스)
- LLM 지식 기반 최신 이슈 분석
"""

from __future__ import annotations
import os
import json
import time as _time
from datetime import datetime, timezone, timedelta
from typing import Any

import requests

from agents.core.base import AgentBase
from agents.core.protocol import AgentMessage

KST = timezone(timedelta(hours=9))

# ── 이슈 카테고리 및 가중치 ──
ISSUE_CATEGORIES = {
    "geopolitical": {
        "weight": 3.0,
        "keywords": ["war", "military", "iran", "missile", "nuclear", "sanction",
                     "invasion", "conflict", "attack", "strike", "전쟁", "군사",
                     "이란", "미사일", "제재", "침공", "공격", "폭격", "tariff", "관세",
                     "trade war", "trade tension", "embargo", "duties", "무역전쟁",
                     "보복관세", "수출규제", "수입규제", "retaliation", "countermeasure",
                     "escalation", "geopoliti", "cold war"],
        "desc": "지정학적 리스크 (전쟁, 제재)",
    },
    "regulation": {
        "weight": 2.5,
        "keywords": ["sec", "regulation", "ban", "lawsuit", "enforcement",
                     "crackdown", "illegal", "규제", "소송", "금지", "단속",
                     "cftc", "doj", "indictment"],
        "desc": "규제/법적 리스크",
    },
    "macro_shock": {
        "weight": 2.0,
        "keywords": ["fed", "fomc", "rate cut", "rate hike", "inflation",
                     "recession", "bank failure", "금리", "인플레", "경기침체",
                     "은행 파산", "default", "debt ceiling", "채무한도",
                     "cpi", "ppi", "unemployment", "실업", "gdp", "stagflation",
                     "quantitative", "liquidity", "유동성", "treasury yield"],
        "desc": "거시경제 충격",
    },
    "exchange_risk": {
        "weight": 2.5,
        "keywords": ["hack", "exploit", "rug pull", "insolvency", "bankrupt",
                     "freeze", "withdrawal halt", "해킹", "탈취", "파산",
                     "출금 중단", "depegged", "디페깅"],
        "desc": "거래소/프로토콜 리스크",
    },
    "adoption": {
        "weight": 1.5,
        "keywords": ["etf", "approval", "institutional", "blackrock", "fidelity",
                     "adoption", "승인", "기관", "도입", "partnership", "제휴",
                     "treasury", "reserve"],
        "desc": "기관 채택/긍정 뉴스",
    },
    "whale_movement": {
        "weight": 1.5,
        "keywords": ["whale", "large transfer", "mt gox", "고래", "대량 이체",
                     "government sell", "정부 매도", "unlock", "잠금 해제",
                     "vesting", "베스팅"],
        "desc": "대규모 물량 이동",
    },
}

# 감성 점수 기준
SENTIMENT_THRESHOLDS = {
    "very_negative": -0.6,
    "negative": -0.2,
    "neutral": 0.2,
    "positive": 0.6,
    # > 0.6 = very_positive
}


class NewsSentimentAgent(AgentBase):
    """
    뉴스/이슈 분석가

    책임:
    - 최신 크립토 뉴스 수집 및 분류
    - 지정학적 이슈 감지 (전쟁, 제재, 관세 등)
    - 뉴스 임팩트 스코어 산출
    - Breaking News → 즉시 리스크 경고
    - 전체 감성 종합 (bullish/bearish/panic)
    """

    def __init__(self, cryptocompare_key: str = ""):
        super().__init__(
            agent_id="agent_6_news",
            agent_name="뉴스/이슈 분석가",
            role_description="실시간 뉴스 및 지정학적 이슈의 크립토 영향 분석 (Subjective)"
        )
        self._analysis_type = "subjective"
        self._cc_key = cryptocompare_key or os.environ.get("CRYPTOCOMPARE_API_KEY", "")

    def _get_system_prompt(self) -> str:
        return """당신은 크립토 시장에 영향을 미치는 뉴스와 이슈를 분석하는 전문가입니다.

분석 원칙:
- 지정학적 이슈(전쟁, 제재, 관세)는 최우선 리스크 팩터
- 규제 뉴스는 특정 코인뿐 아니라 전체 시장 심리에 영향
- 20배 레버리지 환경에서 Breaking News = 즉시 리스크 재평가
- 뉴스의 "실제 임팩트"와 "감성 과잉반응"을 구분
- 이미 가격에 반영된(priced-in) 뉴스 vs 새로운 뉴스 구분

반드시 JSON 형식으로 출력:
```json
{
  "overall_sentiment": "very_bullish|bullish|neutral|bearish|very_bearish|panic",
  "sentiment_score": -1.0~1.0,
  "breaking_news": [
    {"headline": "제목", "category": "카테고리", "impact_score": 1~10, "direction": "bullish|bearish|neutral"}
  ],
  "geopolitical_risk": "low|moderate|elevated|high|critical",
  "regulatory_risk": "low|moderate|high",
  "key_narratives": ["현재 시장을 지배하는 주요 서사 1", "서사 2"],
  "risk_level": "SAFE|CAUTION|DANGER",
  "confidence": 0.0~1.0,
  "reasoning": "종합 판단 근거"
}
```"""

    # ── 뉴스 수집 ──

    def _fetch_cryptocompare_news(self) -> list[dict]:
        """CryptoCompare News API (무료, 키 없어도 제한적 사용 가능)"""
        try:
            url = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&sortOrder=latest"
            headers = {}
            if self._cc_key:
                headers["authorization"] = f"Apikey {self._cc_key}"
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.ok:
                data = resp.json()
                articles = data.get("Data", [])
                # API가 dict를 반환하는 경우 list로 변환
                if isinstance(articles, dict):
                    articles = list(articles.values()) if articles else []
                if not isinstance(articles, list):
                    print(f"[뉴스] CryptoCompare Data 타입 이상: {type(articles)}")
                    articles = []
                result = [
                    {
                        "title": a.get("title", ""),
                        "body": a.get("body", "")[:500],
                        "source": a.get("source", ""),
                        "published_on": a.get("published_on", 0),
                        "categories": a.get("categories", ""),
                        "url": a.get("url", ""),
                    }
                    for a in articles[:20]
                    if isinstance(a, dict)
                ]
                print(f"[뉴스] CryptoCompare: {len(result)}건 수집")
                return result
            else:
                print(f"[뉴스] CryptoCompare 응답 오류: {resp.status_code} {resp.text[:100]}")
        except Exception as e:
            print(f"[뉴스] CryptoCompare 수집 실패: {e}")
        return []

    def _fetch_fear_greed_index(self) -> dict:
        """Alternative.me 공포탐욕지수 (무료, 키 불필요)"""
        try:
            url = "https://api.alternative.me/fng/?limit=3&format=json"
            resp = requests.get(url, timeout=10)
            if resp.ok:
                data = resp.json().get("data", [])
                if data:
                    current = data[0]
                    result = {
                        "value": int(current.get("value", 50)),
                        "label": current.get("value_classification", "Neutral"),
                        "timestamp": current.get("timestamp", ""),
                    }
                    # 이전 값과 비교 (추세)
                    if len(data) >= 2:
                        prev = int(data[1].get("value", 50))
                        result["prev_value"] = prev
                        result["change"] = result["value"] - prev
                    print(f"[뉴스] 공포탐욕지수: {result['value']} ({result['label']})")
                    return result
            else:
                print(f"[뉴스] 공포탐욕지수 응답 오류: {resp.status_code}")
        except Exception as e:
            print(f"[뉴스] 공포탐욕지수 수집 실패: {e}")
        return {}

    # ── 이슈 분류 & 임팩트 계산 ──

    @staticmethod
    def _classify_news(articles: list[dict]) -> dict:
        """
        뉴스를 카테고리별로 분류하고 임팩트 스코어 계산.
        키워드 매칭 기반 (LLM 분석 전 사전 필터링).
        """
        classified = {cat: [] for cat in ISSUE_CATEGORIES}
        impact_scores = {cat: 0.0 for cat in ISSUE_CATEGORIES}

        for article in articles:
            text = (article.get("title", "") + " " + article.get("body", "")).lower()

            for cat, info in ISSUE_CATEGORIES.items():
                matched_keywords = [kw for kw in info["keywords"] if kw.lower() in text]
                if matched_keywords:
                    # 최근 뉴스일수록 가중치 높음
                    published = article.get("published_on", 0)
                    recency = 1.0
                    if published:
                        age_hours = (_time.time() - published) / 3600
                        recency = max(1.0 - age_hours / 24, 0.3)  # 24시간 내 감쇠

                    impact = len(matched_keywords) * info["weight"] * recency
                    classified[cat].append({
                        "title": article.get("title", ""),
                        "keywords": matched_keywords,
                        "impact": round(impact, 2),
                        "source": article.get("source", ""),
                    })
                    impact_scores[cat] += impact

        return {
            "classified": {k: v for k, v in classified.items() if v},
            "impact_scores": {k: round(v, 2) for k, v in impact_scores.items() if v > 0},
        }

    # ── 감성 종합 ──

    @staticmethod
    def _compute_sentiment(classified: dict, impact_scores: dict,
                           fear_greed: dict | None = None) -> dict:
        """
        카테고리별 임팩트 + 공포탐욕지수 → 종합 감성 산출.
        부정 카테고리(전쟁, 규제, 해킹)는 감성을 하락시키고,
        긍정 카테고리(채택)는 상승시킴.
        """
        negative_cats = {"geopolitical", "regulation", "exchange_risk", "macro_shock", "whale_movement"}
        positive_cats = {"adoption"}

        neg_score = sum(impact_scores.get(c, 0) for c in negative_cats)
        pos_score = sum(impact_scores.get(c, 0) for c in positive_cats)

        # -1 ~ +1 정규화 (부정이 더 큰 영향)
        total = neg_score + pos_score
        if total > 0:
            raw = (pos_score - neg_score) / max(total, 1)  # 1.2배 부정 가중치 제거
            sentiment_score = max(min(raw, 1.0), -1.0)
        else:
            sentiment_score = 0.0

        # 공포탐욕지수 반영 (뉴스 키워드 매칭 없을 때 특히 유용)
        fg = fear_greed or {}
        fg_value = fg.get("value")
        if fg_value is not None:
            # 0~100 → -1~+1 변환 (50=중립)
            fg_sentiment = (fg_value - 50) / 50
            if total > 0:
                # 뉴스 데이터 있으면 30% 반영
                sentiment_score = sentiment_score * 0.7 + fg_sentiment * 0.3
            else:
                # 뉴스 키워드 매칭 없으면 공포탐욕지수가 주력
                sentiment_score = fg_sentiment * 0.8

        # 라벨
        if sentiment_score <= -0.6:
            label = "panic"
        elif sentiment_score <= -0.3:
            label = "very_bearish"
        elif sentiment_score <= -0.1:
            label = "bearish"
        elif sentiment_score >= 0.5:
            label = "very_bullish"
        elif sentiment_score >= 0.2:
            label = "bullish"
        else:
            label = "neutral"

        # 지정학 리스크 레벨
        geo_score = impact_scores.get("geopolitical", 0)
        if geo_score >= 8:
            geo_risk = "critical"
        elif geo_score >= 5:
            geo_risk = "high"
        elif geo_score >= 3:
            geo_risk = "elevated"
        elif geo_score >= 1:
            geo_risk = "moderate"
        else:
            geo_risk = "low"

        # 규제 리스크
        reg_score = impact_scores.get("regulation", 0)
        reg_risk = "high" if reg_score >= 5 else ("moderate" if reg_score >= 2 else "low")

        return {
            "sentiment_score": round(sentiment_score, 3),
            "overall_sentiment": label,
            "geopolitical_risk": geo_risk,
            "regulatory_risk": reg_risk,
        }

    # ── Breaking News 추출 ──

    @staticmethod
    def _extract_breaking(classified: dict) -> list[dict]:
        """임팩트 스코어 상위 뉴스 추출 (Breaking News)"""
        all_news = []
        for cat, articles in classified.items():
            for a in articles:
                all_news.append({
                    "headline": a["title"],
                    "category": cat,
                    "impact_score": min(round(a["impact"] * 2), 10),  # 1~10 스케일
                    "direction": "bearish" if cat in ("geopolitical", "regulation",
                                                       "exchange_risk", "macro_shock") else "bullish",
                    "source": a.get("source", ""),
                })

        # 임팩트 상위 5개
        return sorted(all_news, key=lambda x: x["impact_score"], reverse=True)[:5]

    def collect_data(self) -> dict:
        """뉴스 데이터 수집"""
        data = {"articles": [], "timestamp": datetime.now(KST).isoformat()}

        # 1. CryptoCompare 뉴스
        cc_news = self._fetch_cryptocompare_news()
        data["articles"].extend(cc_news)

        # 2. 공포탐욕지수 (Alternative.me)
        data["fear_greed"] = self._fetch_fear_greed_index()

        if not data["articles"]:
            print("[뉴스] ⚠️ 뉴스 수집 실패 — CryptoCompare 0건. API 상태 확인 필요")

        # 3. 키워드 분류
        classification = self._classify_news(data["articles"])
        data["classified"] = classification["classified"]
        data["impact_scores"] = classification["impact_scores"]

        # 4. 감성 계산 (공포탐욕지수 반영)
        sentiment = self._compute_sentiment(
            data["classified"], data["impact_scores"], data.get("fear_greed", {}))
        data["sentiment"] = sentiment

        # 5. Breaking News 추출
        data["breaking"] = self._extract_breaking(data["classified"])

        return data

    def analyze(self, collected_data: dict, context: dict | None = None,
                crash_context: dict | None = None) -> AgentMessage:
        """뉴스/이슈 종합 분석 (급락 시 원인 조사 모드 활성화)"""
        articles = collected_data.get("articles", [])
        classified = collected_data.get("classified", {})
        impact_scores = collected_data.get("impact_scores", {})
        sentiment = collected_data.get("sentiment", {})
        breaking = collected_data.get("breaking", [])
        fear_greed = collected_data.get("fear_greed", {})

        # LLM 분석 (뉴스 헤드라인 기반 심층 해석)
        headlines = [a.get("title", "") for a in articles[:15] if a.get("title")]

        # 뉴스가 없어도 LLM에게 최신 상황 분석 요청 (LLM 지식 기반)
        if headlines:
            breaking_text = ""
            for b in breaking[:3]:
                breaking_text += (f"- [{b['category']}] {b['headline']} "
                                  f"(임팩트: {b['impact_score']}/10, {b['direction']})\n")

            classified_text = ""
            for cat, items in classified.items():
                if items:
                    cat_desc = ISSUE_CATEGORIES.get(cat, {}).get("desc", cat)
                    classified_text += f"\n### {cat_desc} ({len(items)}건)\n"
                    for item in items[:3]:
                        classified_text += f"- {item['title']}\n"

            # 급락 컨텍스트가 있으면 원인 조사 모드
            crash_section = ""
            if crash_context:
                crash_section = f"""
## ⚠️ 긴급: BTC 급락 발생 중
- BTC {crash_context.get('window', '')} 변동: {crash_context.get('change_pct', 0):+.2f}%
- 현재가: ${crash_context.get('current_price', 0):,.0f}
- 기준가: ${crash_context.get('ref_price', 0):,.0f}
- 20x ROE 영향: {crash_context.get('roe_impact', 0):+.1f}%
- 청산까지: {crash_context.get('remaining_to_liq', 5.0):.2f}%

**최우선 임무: 이 급락의 원인을 반드시 파악하세요.**
뉴스, 지정학적 이슈, 규제, 해킹, 고래 매도 등에서 급락 트리거를 찾으세요.
원인을 찾지 못하면 "기술적 매도 압력" 또는 "레버리지 청산 캐스케이드"로 추정하세요.
"""

            prompt = f"""다음 크립토 뉴스를 분석하여 시장 영향을 평가하세요.
{crash_section}
## 최신 헤드라인 (최근 24시간)
{chr(10).join(f'- {h}' for h in headlines[:10])}

## Breaking News (고임팩트)
{breaking_text or "특이 사항 없음"}

## 카테고리별 분류
{classified_text or "분류된 뉴스 없음"}

## 공포탐욕지수 (Fear & Greed Index)
- 현재: {fear_greed.get('value', 'N/A')} ({fear_greed.get('label', 'N/A')})
- 전일 대비: {fear_greed.get('change', 'N/A'):+d}p
{f"- 추세: {'공포 심화' if fear_greed.get('change', 0) < -5 else '공포 완화' if fear_greed.get('change', 0) > 5 else '유지'}" if fear_greed.get('change') is not None else ''}

## 사전 계산된 감성
- 감성 점수: {sentiment.get('sentiment_score', 0):.2f}
- 지정학 리스크: {sentiment.get('geopolitical_risk', 'N/A')}
- 규제 리스크: {sentiment.get('regulatory_risk', 'N/A')}

## 분석 요청
1. {'이 급락의 직접적 원인/트리거 식별' if crash_context else '현재 시장을 지배하는 주요 서사(narrative) 식별'}
2. 지정학적 이슈(전쟁, 제재, 관세)가 크립토에 미치는 영향
3. 이미 가격에 반영된 뉴스 vs 새로운 리스크 구분
4. 20배 레버리지 환경에서 주의할 점
{'5. 급락이 단기 조정인지, 추세 전환의 시작인지 판단' if crash_context else ''}

반드시 JSON 형식으로 출력하세요.{' crash_cause 필드에 급락 원인을 명시하세요.' if crash_context else ''}"""

            result = self.llm_json(prompt, deep=True, max_tokens=2000)

            if not result.get("parse_error"):
                # LLM 결과에 사전 계산 데이터 병합
                result.setdefault("sentiment_score", sentiment.get("sentiment_score", 0))
                result.setdefault("geopolitical_risk", sentiment.get("geopolitical_risk", "low"))
                result.setdefault("regulatory_risk", sentiment.get("regulatory_risk", "low"))
                result.setdefault("breaking_news", breaking)
                if fear_greed:
                    result["fear_greed_index"] = fear_greed.get("value")
                    result["fear_greed_label"] = fear_greed.get("label")
            else:
                result = self._fallback_analysis(sentiment, breaking, impact_scores)
        else:
            # 뉴스 수집 실패 시에도 LLM 지식 기반 분석 시도
            no_data_prompt = f"""현재 뉴스 API에서 데이터를 수집하지 못했습니다.
당신의 학습 데이터와 최근 지식을 기반으로 크립토 시장에 영향을 미치는 주요 이슈를 분석해주세요.

{f'''## ⚠️ 긴급: BTC 급락 발생 중
- 변동: {crash_context.get("change_pct", 0):+.2f}%
- 현재가: ${crash_context.get("current_price", 0):,.0f}
최우선: 급락 원인 파악''' if crash_context else ''}

주요 분석 항목:
1. 현재 글로벌 지정학적 이슈 (전쟁, 관세, 무역갈등)
2. 주요 규제/정책 변화
3. 거시경제 이벤트 (금리, CPI, 고용 등)

반드시 JSON 형식으로 출력하세요."""
            llm_result = self.llm_json(no_data_prompt, deep=True, max_tokens=1500)
            if not llm_result.get("parse_error"):
                llm_result.setdefault("confidence", 0.3)  # 뉴스 데이터 없으므로 낮은 신뢰도
                llm_result.setdefault("reasoning",
                    (llm_result.get("reasoning", "") + " (뉴스 API 수집 실패, LLM 지식 기반 분석)").strip())
                result = llm_result
            else:
                result = self._fallback_analysis(sentiment, breaking, impact_scores)
                result["reasoning"] += " | ⚠️ 뉴스 API 수집 실패"

        # 경고 생성
        warnings = self._generate_warnings(result, sentiment, breaking)

        return self._build_message(
            data=result,
            confidence=result.get("confidence", 0.5),
            reasoning=result.get("reasoning", ""),
            warnings=warnings
        )

    def _fallback_analysis(self, sentiment: dict, breaking: list,
                            impact_scores: dict) -> dict:
        """규칙 기반 폴백"""
        geo_risk = sentiment.get("geopolitical_risk", "low")
        reg_risk = sentiment.get("regulatory_risk", "low")
        sent_score = sentiment.get("sentiment_score", 0)

        # 리스크 레벨
        if geo_risk in ("critical", "high") or sent_score <= -0.5:
            risk_level = "DANGER"
        elif geo_risk == "elevated" or reg_risk == "high" or sent_score <= -0.2:
            risk_level = "CAUTION"
        else:
            risk_level = "SAFE"

        return {
            "overall_sentiment": sentiment.get("overall_sentiment", "neutral"),
            "sentiment_score": sent_score,
            "breaking_news": breaking,
            "geopolitical_risk": geo_risk,
            "regulatory_risk": reg_risk,
            "key_narratives": [],
            "risk_level": risk_level,
            "confidence": 0.4,
            "reasoning": (f"규칙 기반: 감성 {sent_score:+.2f}, "
                          f"지정학 {geo_risk}, 규제 {reg_risk}")
        }

    @staticmethod
    def _generate_warnings(result: dict, sentiment: dict, breaking: list) -> list[str]:
        """분석 결과에서 경고 생성"""
        warnings = []

        geo_risk = result.get("geopolitical_risk", sentiment.get("geopolitical_risk", "low"))
        if geo_risk in ("critical", "high"):
            warnings.append(f"🚨 지정학 리스크 {geo_risk.upper()} — 포지션 축소 권고")
        elif geo_risk == "elevated":
            warnings.append(f"⚠️ 지정학 리스크 상승 — 모니터링 강화")

        reg_risk = result.get("regulatory_risk", "low")
        if reg_risk == "high":
            warnings.append("⚖️ 규제 리스크 HIGH — 관련 코인 주의")

        # Breaking News 경고
        for b in breaking[:2]:
            if b.get("impact_score", 0) >= 7:
                warnings.append(f"🔴 [{b['category']}] {b['headline'][:60]}")
            elif b.get("impact_score", 0) >= 5:
                warnings.append(f"🟡 [{b['category']}] {b['headline'][:60]}")

        sent = result.get("overall_sentiment", "neutral")
        if sent in ("panic", "very_bearish"):
            warnings.append(f"📉 시장 감성: {sent} — 20x 레버리지 극도 주의")

        return warnings
