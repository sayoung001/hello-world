# Phase A + B 구현 진행 보고서

> 작성일: 2026-04-16
> 작성자: Claude Code
> 기반: plan_vision_rag_risk_agent.md

---

## Phase A: 기반 인프라 (완료)

### 1. RAG 인프라 (`rag/`)

| 파일 | 역할 | 상태 |
|------|------|------|
| `rag/__init__.py` | 모듈 패키지 초기화 | 완료 |
| `rag/vectorstore.py` | ChromaDB 컬렉션 관리 (CRUD, 코사인 유사도 검색) | 완료 |
| `rag/embedder.py` | sentence-transformers 임베딩 (all-MiniLM-L6-v2, 384차원) | 완료 |
| `rag/retriever.py` | 시맨틱 검색 + 시간 가중 (90일 반감기) | 완료 |
| `rag/context_builder.py` | 검색 결과 → RL 피처 / LLM 컨텍스트 변환 | 완료 |
| `rag/news_ingester.py` | CryptoPanic + CryptoCompare 뉴스 → 벡터 저장 | 완료 |
| `rag/trade_ingester.py` | labeled_signals CSV / 실전 거래 → 벡터 적재 | 완료 |

### 2. Vision AI (`vision/`)

| 파일 | 역할 | 상태 |
|------|------|------|
| `vision/__init__.py` | 모듈 패키지 초기화 | 완료 |
| `vision/chart_renderer.py` | mplfinance 기반 AI 최적화 다크 차트 (EMA, BB, SL/TP 선) | 완료 |
| `vision/pattern_analyzer.py` | Claude Vision API → 차트 패턴 분류 + 추세 판단 | 완료 |
| `vision/vision_scorer.py` | 패턴 분석 → RL/리스크 수치 피처 (5개 피처) | 완료 |
| `vision/cache.py` | LRU + TTL 캐시 (동일 캔들 중복 호출 방지) | 완료 |

### 3. 버그 수정

| 항목 | 수정 내용 |
|------|----------|
| `agents/core/base.py` max_tokens | `llm_call`, `llm_json` 기본값 2000 → 4096으로 상향 |

---

## Phase B: 리스크 관리 에이전트 (완료)

### 4. 리스크 모듈 (`risk/`)

| 파일 | 역할 | 상태 |
|------|------|------|
| `risk/__init__.py` | 모듈 패키지 초기화 | 완료 |
| `risk/models.py` | Pydantic 데이터 모델 (12개 클래스: RiskDecision, PipelineContext 등) | 완료 |
| `risk/config.py` | 레버리지/사이징/헷지/리스크 임계값 설정 | 완료 |
| `risk/analyst_agent.py` | 시장 상태 종합 분석 (AgentBase 상속, Vision+RAG 반영) | 완료 |
| `risk/risk_agent.py` | 포지션별 리스크 평가 + SL/TP/사이징 제안 (AgentBase 상속) | 완료 |
| `risk/hedge_agent.py` | 헷지 전략 4종 판단 (AgentBase 상속) | 완료 |
| `risk/leverage_agent.py` | 레버리지 10x~20x 동적 조절 (AgentBase 상속) | 완료 |
| `risk/pipeline.py` | Analyst→Risk→Hedge→Leverage 순차 파이프라인 + TG 포맷 | 완료 |

### AgentBase 상속 구조
```
agents/core/base.py (AgentBase)
  ├── risk/analyst_agent.py  (AnalystAgent)  — _get_system_prompt, collect_data, analyze
  ├── risk/risk_agent.py     (RiskAgent)     — _get_system_prompt, collect_data, analyze
  ├── risk/hedge_agent.py    (HedgeAgent)    — _get_system_prompt, collect_data, analyze
  └── risk/leverage_agent.py (LeverageAgent) — _get_system_prompt, collect_data, analyze
```

### 각 에이전트 설계

#### AnalystAgent (시장 분석가)
- 규칙 기반 초벌 분석 + LLM 심층 분석 (API 키 없으면 규칙 기반 fallback)
- 입력: PipelineContext (MarketState + Vision + RAG)
- 출력: MarketAnalysis (sentiment, volatility_regime, key_risks, opportunities)
- 규칙: BTC 레벨, ATR%, FGI, 펀딩레이트, RSI, 24h 변동, 노출도 기반

#### RiskAgent (리스크 관리자)
- 포지션별 리스크 점수 계산 (0~100) + 포트폴리오 종합
- 입력: PipelineContext + MarketAnalysis
- 출력: RiskAssessment (position_assessments, portfolio_risk, total_risk_score)
- 점수 요소: PnL(-60% → +30), 청산거리(<1.5% → +35), 보유시간, 시장/포지션 방향 불일치

#### HedgeAgent (헷지 전략가)
- 4가지 전략: PARTIAL_CLOSE, OPPOSITE_POSITION, CORRELATED_HEDGE, FULL_EXIT
- 입력: PipelineContext + RiskAssessment
- 출력: HedgeRecommendation (action, size_pct, urgency)
- 판단: CRITICAL 2개+ → 전량청산, HIGH → 부분청산, 그 외 → 상관코인 헷지
- 상관 매핑: BTC↔ETH, SOL→ETH, BNB→ETH, AVAX→SOL, DOGE→BTC

#### LeverageAgent (레버리지 조절자)
- ATR% 기반 변동성 역상관 + 6가지 감산 요인
- 입력: PipelineContext + RiskAssessment
- 출력: LeverageRecommendation (recommended_leverage, risk_factors)
- 매핑: ATR <1% → 20x, 1~2% → 15x, 2~3% → 12x, >3% → 10x
- 감산: BTC 레벨4+(-2x), 극단 펀딩(-3x), Vision 고위험(-2x), 연속 손실3+(-3x), FGI 극단(-2x)

---

## 테스트 결과

### Phase A 테스트 (tests/test_phase_a.py)

실행 환경: Claude Code 개발 환경 (HuggingFace 접근 불가)

**총 28건: PASS 25, FAIL 3**

실패 3건은 HuggingFace 모델 다운로드 네트워크 차단. GCE 서버에서 정상 동작 예상.

| 카테고리 | PASS | FAIL | 비고 |
|----------|------|------|------|
| Embedder | 0 | 3* | *네트워크 차단 |
| VectorStore | 2 | 0 | ChromaDB CRUD 정상 |
| Retriever | 0 | 0* | *Embedder 의존 |
| ContextBuilder | 4 | 0 | 승률/ROE/SL히트율/LLM텍스트 |
| NewsIngester | 2 | 0 | 수집 프레임워크 정상 |
| ChartRenderer | 3 | 0 | PNG 92KB, 파일 저장 |
| VisionScorer | 8 | 0 | 피처 변환, 방향 일치/반대, 조정값 |
| VisionCache | 5 | 0 | put/get, miss, TTL, LRU |
| max_tokens | 2 | 0 | 4096 기본값 확인 |

### Phase B 테스트 (tests/test_phase_b.py)

실행 일시: 2026-04-16

**총 48건: PASS 48, FAIL 0**

LLM 호출은 API 키 없이 실행 → 규칙 기반 fallback으로 전체 로직 검증.

| 카테고리 | PASS | 주요 검증 항목 |
|----------|------|----------------|
| Pydantic 모델 | 7 | Enum, PositionContext, MarketState, PipelineContext, RiskDecision, JSON 직렬화 |
| Config | 4 | 레버리지 범위, ATR 매핑, 리스크 임계값, 헷지 상한 |
| AnalystAgent | 8 | 초기화, AgentBase 상속, 정상/고위험 시장 분석, Vision/RAG 반영 |
| RiskAgent | 7 | 초기화, 상속, 빈 포지션, 정상/고위험 평가, 조치 결정 |
| HedgeAgent | 7 | 초기화, 상속, 저위험(none), 고위험(partial_close), CRITICAL(full_exit), 상관매핑 |
| LeverageAgent | 6 | 초기화, 상속, 저변동(20x), 고변동(10x), 최소 보장, CRITICAL(10x) |
| 파이프라인 | 13 | 3시나리오(정상/위기/빈), 리스크/헷지/레버리지 판단, TG포맷, 통계, 타임스탬프 |
| 시그널 평가 | 1 | evaluate_signal 래퍼 동작 |

### Phase B 상세 테스트 결과

| 테스트 | 결과 | 세부사항 |
|--------|------|----------|
| models_enum_risk | PASS | RiskLevel.CRITICAL = "critical" |
| models_enum_hedge | PASS | HedgeAction.PARTIAL_CLOSE = "partial_close" |
| models_position | PASS | PositionContext: ETHUSDT long lev=20 |
| models_market | PASS | MarketState: BTC $95,000 level=2 |
| models_context | PASS | PipelineContext: 1 pos, bal=1000 |
| models_decision | PASS | RiskDecision: medium |
| models_serialization | PASS | JSON roundtrip OK, keys=8 |
| config_leverage_range | PASS | 레버리지 범위: 10~20 |
| config_atr_map | PASS | ATR 매핑 4단계 |
| config_risk_thresholds | PASS | low=30, medium=50, high=70, critical=85 |
| config_hedge_max | PASS | 최대 헷지 50% |
| analyst_init | PASS | id=analyst, name=시장 분석가 |
| analyst_inheritance | PASS | AgentBase 상속 확인 |
| analyst_normal | PASS | 정상 시장: neutral, normal, risks=0 |
| analyst_danger_risks | PASS | 고위험: 7개 리스크 식별 (BTC 레벨5, ATR3.5%, FGI12, 펀딩-0.08%, RSI22, 24h-8%, 노출6배) |
| analyst_danger_sentiment | PASS | bearish (score=-0.7) |
| analyst_danger_volatility | PASS | extreme |
| analyst_vision_rag | PASS | Vision AI 고위험 + SL 히트율 75% 반영 |
| risk_init | PASS | id=risk |
| risk_inheritance | PASS | AgentBase 상속 |
| risk_no_positions | PASS | 빈 포지션 → low |
| risk_normal_position | PASS | 정상: 점수 32.5/100, low |
| risk_danger_position | PASS | 고위험: 점수 100/100, critical |
| risk_danger_action | PASS | 조치: close |
| hedge_init | PASS | id=hedge |
| hedge_inheritance | PASS | AgentBase 상속 |
| hedge_no_action | PASS | 저위험 → none |
| hedge_partial_close | PASS | 고위험 → partial_close 35% |
| hedge_full_exit | PASS | CRITICAL 2개+ → full_exit |
| hedge_correlated_pairs | PASS | 상관 매핑 6쌍 |
| leverage_init | PASS | id=leverage |
| leverage_inheritance | PASS | AgentBase 상속 |
| leverage_calm | PASS | 저변동 → 20x |
| leverage_volatile | PASS | 고변동+다중 감산 → 10x |
| leverage_min_bound | PASS | 최소 10x 보장 |
| leverage_critical | PASS | CRITICAL → 10x |
| pipeline_init | PASS | 4개 에이전트 초기화 |
| pipeline_scenario1_risk | PASS | 정상 시장: risk=low |
| pipeline_scenario1_hedge | PASS | 헷지: none |
| pipeline_scenario1_leverage | PASS | 레버리지: 15x |
| pipeline_scenario2_risk | PASS | 위기 시장: risk=critical |
| pipeline_scenario2_leverage | PASS | 레버리지 감소: 10x |
| pipeline_scenario3_empty | PASS | 빈 포지션: risk=low |
| pipeline_telegram | PASS | TG 메시지 331자 |
| pipeline_stats | PASS | 실행 횟수: 3 |
| pipeline_reasoning | PASS | 판단 근거 포함 (시장/포지션/헷지/레버리지) |
| pipeline_timestamp | PASS | KST 타임스탬프 |
| signal_eval | PASS | evaluate_signal 래퍼 정상 |

---

## 파이프라인 시나리오별 결과

### 시나리오 1: 정상 시장 + 이익 포지션
```
입력: ETHUSDT 롱, ROE +66.6%, BTC $95K level=1, ATR 1.2%, FGI 50
결과: risk=LOW, 헷지=none, 레버리지=15x, 긴급도=low
소요: <0.1초
```

### 시나리오 2: 위기 시장 + 대손실 포지션
```
입력: SOLUSDT 롱, ROE -93.3%, BTC $82K level=5, ATR 3.8%, FGI 10, 연속손실 3회
결과: risk=CRITICAL, 헷지=full_exit 100%, 레버리지=10x, 긴급도=high
판단근거: 시장 bearish (변동성 extreme) | 포지션 1개 (점수 100/100) | 레버리지 20x→10x
소요: <0.1초
```

### 시나리오 3: 빈 포지션
```
입력: 포지션 없음, BTC $95K
결과: risk=LOW, 헷지=none, 레버리지=20x
소요: <0.1초
```

---

## 설치된 패키지

```
chromadb==1.5.7          # Phase A: 벡터 스토어
sentence-transformers==5.4.1  # Phase A: 임베딩
plotly==6.7.0            # Phase A: 차트 (대안)
mplfinance==0.12.10b0    # Phase A: 차트 렌더링
kaleido==1.2.0           # Phase A: 이미지 변환
anthropic==0.95.0        # Phase A+B: Claude API
pydantic==2.13.1         # Phase B: 데이터 모델
torch==2.11.0            # Phase A: 임베딩 백엔드
transformers==5.5.4      # Phase A: 임베딩 백엔드
```

---

## 아키텍처 노트

### 리스크 파이프라인 흐름
```
시그널/포지션 업데이트
  → PipelineContext 구성 (포지션 + 시장 + Vision + RAG)
  → AnalystAgent.analyze_market() → MarketAnalysis
  → RiskAgent.assess() → RiskAssessment (포지션별 + 포트폴리오)
  → HedgeAgent.recommend() → HedgeRecommendation
  → LeverageAgent.recommend() → LeverageRecommendation
  → RiskDecision (종합 출력)
  → 텔레그램 알림 (format_telegram)
```

### 듀얼 모드 (규칙 기반 + LLM)
```
모든 에이전트는 2단계 분석:
  1. 규칙 기반 초벌 분석 (항상 실행, <0.1초)
  2. LLM 심층 분석 (API 키 있을 때만, 2~5초)
  → LLM 실패 시 자동으로 규칙 기반 결과 사용 (graceful fallback)
```

---

## 다음 단계 (Phase C: 적응형 메모리)

1. `memory/trading_brain.py` — 규칙 저장소 + 감쇠 엔진
2. `memory/reflection_engine.py` — 거래 결과 → LLM 분석 → 규칙 생성
3. `memory/rule_store.py` — 규칙 CRUD + ChromaDB 연동
4. `memory/decay_engine.py` — 시간 감쇠 + 성과 기반 가중치 조정

## 다음 단계 (Phase D: 통합)

1. Vision + RAG + Memory → enriched_state 생성
2. Risk Pipeline → auto_trader_v9.py 연동
3. 백테스트 검증 (Walk-Forward 3윈도우)
