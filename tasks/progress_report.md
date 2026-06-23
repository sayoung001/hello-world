# Phase A + B + C + D + E + F 구현 진행 보고서

> 작성일: 2026-04-16 (Phase E: 2026-04-19, Phase F: 2026-04-20)
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

## Phase C: 적응형 메모리 시스템 (완료)

### 5. 메모리 모듈 (`agents/memory/`)

| 파일 | 역할 | 상태 |
|------|------|------|
| `agents/memory/__init__.py` | 모듈 패키지 초기화 (모듈 설명 포함) | 완료 |
| `agents/memory/rule_store.py` | 트레이딩 규칙 CRUD + JSON 영속 + ChromaDB 벡터 검색 (선택) | 완료 |
| `agents/memory/decay_engine.py` | 시간 감쇠 (70일 반감기) + 성과 기반 가중치 조정 | 완료 |
| `agents/memory/trading_brain.py` | 규칙 조회/자문 + 코인별·패턴별 승률 추적 | 완료 |
| `agents/memory/reflection_engine.py` | 거래 종료 → LLM/규칙 기반 분석 → 규칙 자동 생성 | 완료 |

### 각 모듈 설계

#### RuleStore (규칙 저장소)
- `TradingRule`: rule_id, condition, action, confidence, weight, hits/misses, active
- 이중 저장: 메모리 dict (빠른 접근) + JSON 파일 (영속성)
- ChromaDB 연동 선택적 (시맨틱 벡터 기반 유사 규칙 검색)
- CRUD: add, get, update, record_hit, record_miss, deactivate
- 검색: `search_by_condition()` — ChromaDB 시맨틱 검색 / 텍스트 매칭 폴백

#### DecayEngine (감쇠 엔진)
- 시간 감쇠: `weight = base × exp(-λ × days)`, λ=0.01 (70일 반감기)
- 성과 조정: `weight × (적중률 / 0.5)`, 클램프 [0.5, 1.5]
- 비활성화: weight < 0.1 → `active=False`
- 최소 샘플 5건 이상일 때만 성과 조정 적용
- `decay_all()`: 24시간 주기 일괄 실행 권장

#### TradingBrain (트레이딩 브레인)
- `consult(signal, market_state)`: 규칙 검색 → 자문 생성
- 출력: applicable_rules, coin_winrate, pattern_winrate, brain_confidence, advice
- 코인별 승률 추적: `_coin_stats[symbol]` (wins/losses/total_roe)
- 패턴별 승률 추적: `_pattern_stats[gap_bin]` (GAP 구간별)
- 종합 신뢰도: 규칙 평균 + 코인 승률 + 패턴 승률 반영
- `run_maintenance()`: 24시간 주기 감쇠 적용

#### ReflectionEngine (반성 엔진)
- `reflect(trade_record)`: 거래 종료 후 자동 분석
- LLM 모드: Claude Haiku로 교훈 도출 → JSON 파싱 → 규칙 생성
- 규칙 기반 폴백 (LLM 없을 때): 5가지 패턴 매칭
  - 패턴 1: SL 히트 + 방향 정확 → SL 확대 권장
  - 패턴 2: 빠른 TP (20캔들 이내) → 트레일링 고려
  - 패턴 3: 장기 횡보 타임아웃 → 기록만
  - 패턴 4: BTC 고위험 + 큰 손실 → 포지션 축소
  - 패턴 5: 일반 수익/손실 → 기록만

### 메모리 시스템 아키텍처
```
거래 종료 이벤트
  → ReflectionEngine.reflect(trade) → 교훈 도출 + 규칙 생성
  → RuleStore.add() → JSON 파일 저장 (+ ChromaDB 벡터)

시그널 발생
  → TradingBrain.consult(signal) → RuleStore.search_by_condition()
  → applicable_rules + coin_winrate + pattern_winrate
  → brain_confidence + advice 텍스트

24시간 주기
  → TradingBrain.run_maintenance() → DecayEngine.decay_all()
  → 오래된 규칙 가중치 감쇠 + 성과 미달 규칙 비활성화
```

---

## 에이전트 위험 편향 수정

### 발견된 문제
사용자 보고: "에이전트가 항상 위험하다고 판단하는 것 같다"

### 원인 분석 (6개 구조적 편향)

| 파일 | 편향 유형 | 수정 내용 |
|------|----------|----------|
| `agents/core/orchestrator.py` | 시스템 프롬프트 Bear 편향 | "Bear의 우려가 구체적이면 보수적 판단" → "양측 근거 공정 평가 (Bear 편향 주의)" |
| `agents/core/orchestrator.py` | 기본값 CAUTION | `overall_risk` 기본값 "CAUTION" → "SAFE" |
| `agents/core/orchestrator.py` | DANGER 임계값 너무 낮음 | danger_pct ≥ 0.30 → ≥ 0.45, danger+caution ≥ 0.40 → ≥ 0.55 |
| `agents/core/orchestrator.py` | risk-off 판단 너무 쉬움 | danger_count ≥ 2 → ≥ 3, bearish ≥ 3 → ≥ 4, safe 비율 0.6 → 0.4 |
| `agents/analyzers/news_sentiment.py` | 부정 뉴스 1.2배 가중 | `neg_score × 1.2` → `neg_score × 1.0` (동등 가중) |
| `agents/analyzers/correlation.py` | 데이터 부족 시 CAUTION | 기본 risk_level "CAUTION" → "SAFE", confidence 0.4 → 0.3 |
| `agents/analyzers/position_judge.py` | DANGER 신뢰도 부풀림 | DANGER confidence 0.85 → 0.75 (SAFE와 동등화) |

---

## Phase C 테스트 결과

### Phase C 테스트 (tests/test_phase_c.py)

실행 일시: 2026-04-16

**총 50건: PASS 50, FAIL 0**

LLM 없이 규칙 기반 폴백으로 전체 로직 검증.

| 카테고리 | PASS | 주요 검증 항목 |
|----------|------|----------------|
| TradingRule | 6 | 생성, hit_rate, effective_confidence, sample_size, to_text, 직렬화 |
| RuleStore | 10 | 초기화, add, get, update, hit/miss, deactivate, active_rules, search, stats, 영속성 |
| DecayEngine | 10 | 초기화, should_run, 30일 감쇠, 70일 반감기, 적중률 보너스/패널티, 일괄 감쇠, 비활성화, preview |
| TradingBrain | 10 | 초기화, consult keys/rules/confidence, 시장 상태, 승률 업데이트, stats, top_coins, maintenance |
| ReflectionEngine | 10 | 초기화, SL 히트/규칙 생성, 빠른 TP, 타임아웃, BTC 위험/규칙, 일반 수익, stats, store 반영 |
| 통합 테스트 | 4 | 반성→규칙 생성, 자문 반영, 승률 업데이트, store 공유 |

### Phase C 상세 테스트 결과

| 테스트 | 결과 | 세부사항 |
|--------|------|----------|
| rule_create | PASS | id=test001, conf=0.7 |
| rule_hit_rate | PASS | hit_rate=0.80 (8/10) |
| rule_effective_conf | PASS | effective=0.630 (0.7×0.9) |
| rule_sample_size | PASS | sample_size=10 |
| rule_to_text | PASS | "조건: ETH 수렴 후 롱..." |
| rule_serialization | PASS | roundtrip OK, keys=11 |
| store_init | PASS | empty store, count=0 |
| store_add | PASS | added 2 rules, count=2 |
| store_get | PASS | fetched rule OK |
| store_update | PASS | conf=0.8, weight=0.95 |
| store_hit_miss | PASS | hits=2, misses=1 |
| store_deactivate | PASS | active=False |
| store_active_rules | PASS | active=1, total=2 |
| store_search | PASS | 텍스트 매칭 검색 정상 |
| store_stats | PASS | total=2, active=1 |
| store_persistence | PASS | reloaded count=2 |
| decay_init | PASS | lambda=0.01 |
| decay_should_run | PASS | 첫 실행 → True |
| decay_30days | PASS | 30일: 0.7408 |
| decay_70days_halflife | PASS | 70일: 0.4966 < 0.55 |
| decay_perf_boost | PASS | 적중률 80% → 1.4851 > 0.9900 |
| decay_perf_penalty | PASS | 적중률 10% → 0.4950 < 0.9900 |
| decay_all_run | PASS | decayed=0, deactivated=1 |
| decay_deactivate_old | PASS | 300일 규칙: active=False, weight=0.0498 |
| decay_should_not_run | PASS | 방금 실행 → False |
| decay_preview | PASS | preview 동작 확인 |
| brain_init | PASS | rule_store + decay_engine 초기화 |
| brain_consult_keys | PASS | 5개 키 반환 |
| brain_consult_rules | PASS | applicable_rules=1 |
| brain_consult_confidence | PASS | brain_confidence=0.54 |
| brain_consult_market | PASS | 시장 상태 포함 자문 |
| brain_winrate_updated | PASS | ETH winrate=0.6667 (2W/1L) |
| brain_pattern_winrate | PASS | pattern_winrate=0.6667 |
| brain_stats | PASS | coins=2, rules=2 |
| brain_top_coins | PASS | [ETHUSDT, BTCUSDT] |
| brain_maintenance | PASS | 감쇠 실행 |
| reflection_init | PASS | rule_store 연결 |
| reflection_sl_hit | PASS | "방향은 맞았으나 SL이 너무 타이트" |
| reflection_sl_rule_created | PASS | 1개 규칙 자동 생성 |
| reflection_fast_tp | PASS | "빠른 목표 도달" |
| reflection_timeout | PASS | "장기 횡보 후 타임아웃" |
| reflection_btc_danger | PASS | "BTC 레벨 5에서 큰 손실" |
| reflection_btc_rule | PASS | 1개 규칙 자동 생성 |
| reflection_normal_win | PASS | "정상 수익 거래" |
| reflection_stats | PASS | reflections=5, rules=3 |
| reflection_store_populated | PASS | store에 3개 규칙 생성됨 |
| integ_reflect | PASS | 반성 → 1개 규칙 생성 |
| integ_consult_after_reflect | PASS | 반성 규칙이 자문에 반영됨 |
| integ_winrate_reflect | PASS | 손실 기록 → winrate=0.0000 |
| integ_shared_store | PASS | brain과 reflection이 같은 store 공유 |

---

## 누적 테스트 요약

| Phase | 총 | PASS | FAIL | 비고 |
|-------|-----|------|------|------|
| A (RAG + Vision) | 28 | 25 | 3 | HuggingFace 네트워크 차단 (GCE 정상 예상) |
| B (리스크 에이전트) | 48 | 48 | 0 | |
| C (적응형 메모리) | 50 | 50 | 0 | |
| **합계** | **126** | **123** | **3** | |

---

## Phase D: 통합 레이어 (완료)

### 6. 통합 모듈

| 파일 | 역할 | 상태 |
|------|------|------|
| `agents/enriched_state.py` | Vision + RAG + Memory → `PipelineContext` 통합 빌더 | 완료 |
| `agents/v9_hook.py` 확장 | `risk_check()` 메서드 추가 + 자동 반성 루프 | 완료 |

### EnrichedStateBuilder 설계

```
signal + positions + market_data + (선택) vision_analysis
  ↓
EnrichedStateBuilder.build()
  ↓
{
  "pipeline_context": PipelineContext,  # Risk Pipeline 입력
  "brain_advice": dict,                 # TradingBrain.consult() 결과
  "rag_features": dict,                 # RAG 수치 피처 (승률/SL히트율 등)
  "vision_features": dict,              # Vision 수치 피처 (정합도/리스크)
  "rag_text": str,                       # LLM 프롬프트용 자연어 컨텍스트
}
```

**핵심 특징**:
- 외부 모듈 없이도 빈 입력으로 정상 작동 (지연 로딩)
- v9 Position 객체 / dict 양방향 변환 지원
- PnL/ROE 자동 계산 (20x 레버리지 반영)

### v9_hook 확장 (Phase D)

#### 초기화 시 자동 구성
```python
hook = AgentHook(
    enable_risk_pipeline=True,   # Phase B 리스크 파이프라인
    enable_memory=True,          # Phase C 메모리 시스템
)
# 자동 구성:
# - RiskPipeline (Analyst→Risk→Hedge→Leverage)
# - RuleStore + TradingBrain + ReflectionEngine
# - EnrichedStateBuilder (Brain 주입)
```

#### 새로운 API

| 메서드 | 트리거 | 동작 |
|--------|--------|------|
| `risk_check(signal, positions, market_data, ...)` | 진입 전 | 리스크 파이프라인 실행 + 진입 스냅샷 저장 |
| `on_position_close(pos, reason, pnl_pct)` 확장 | 청산 시 | Shadow 기록 + **자동 반성 → 규칙 생성** |
| `get_brain_advice(signal, market_state)` | 임의 | Brain 자문만 조회 |
| `get_memory_stats()` | 임의 | 브레인 + 반성 통계 |

#### 자동 학습 루프
```
진입: risk_check()
  → PipelineContext 빌드 + 리스크 판단 + 스냅샷 저장

청산: on_position_close()
  → 스냅샷 복구 + ReflectionEngine.reflect() → 규칙 생성
  → TradingBrain.record_trade_result() → 승률 업데이트
  → (선택) 텔레그램 보고 "🧠 [Reflection] ..."

다음 진입: risk_check()
  → Brain 자문에 방금 생성된 규칙 반영 → 자문 신뢰도↑
```

### Phase D 테스트 결과 (tests/test_phase_d.py)

실행 일시: 2026-04-19

**총 45건: PASS 45, FAIL 0**

| 카테고리 | PASS | 주요 검증 항목 |
|----------|------|----------------|
| EnrichedStateBuilder (최소) | 7 | 빈 초기화, PipelineContext 생성, 시장/시그널 전달, 빈 피처 |
| EnrichedStateBuilder (포지션) | 6 | dict/객체 변환, PnL/ROE 자동 계산, 총 노출 집계 |
| Builder + Brain 통합 | 4 | Brain keys, 규칙 반환, 승률 반영, summary |
| v9_hook 초기화 | 7 | 모듈 import, RiskPipeline/Brain/Reflection/Builder 생성 |
| risk_check 동작 | 8 | 정상/위기 시장 판단, 레버리지 조정, 스냅샷 저장 |
| 자동 반성 루프 | 5 | 스냅샷 소비, 규칙 생성, brain 업데이트, 다중 거래 |
| 학습 루프 | 4 | 초기 자문, 규칙 학습, 승률 변화, 메모리 통계 |
| 비활성화 플래그 | 4 | risk_pipeline/memory OFF, None 반환 |

### 시나리오별 검증

#### 시나리오 A: 정상 시장 진입
```
입력: ETHUSDT 롱 GAP 0.3%, BTC $95K level 1, ATR 1.2%, FGI 50
결과: risk=low, urgency=low, leverage=15x, approved=True
소요: <0.3초
```

#### 시나리오 B: 위기 시장 + 손실 포지션
```
입력: AVAXUSDT + SOLUSDT(-$20), BTC $82K level 5, ATR 3.8%, FGI 10, 연속손실 3
결과: risk=critical, urgency=high, leverage=10x
소요: <0.1초
```

#### 시나리오 C: 자동 학습 사이클 (3거래)
```
SOLUSDT 롱 GAP 0.4% × 3회 (SL/TP/TP)
→ 반성 3회 실행 (rule_based)
→ SOL 승률 0.5000 → 0.6667 (2W/1L)
→ 다음 시그널 자문에 반영
```

---

## 누적 테스트 요약 (Phase D 시점)

| Phase | 총 | PASS | FAIL | 비고 |
|-------|-----|------|------|------|
| A (RAG + Vision) | 28 | 25 | 3 | HuggingFace 네트워크 차단 |
| B (리스크 에이전트) | 48 | 48 | 0 | |
| C (적응형 메모리) | 50 | 50 | 0 | |
| D (통합 레이어) | 45 | 45 | 0 | |
| **소계** | **171** | **168** | **3** | |

---

## auto_trader_v9.py 연동 가이드

### 기존 AgentHook 사용처 (변경 없음)
```python
# auto_trader_v9.py 내부 (이미 연동됨)
self.agent_hook = AgentHook(self.cg_client, self.btc_trend, self.tg,
                            exchange=self.exchange)
self.agent_hook.crash_check(self.positions)       # 매 15초
self.agent_hook.on_position_open(pos, ...)         # 진입 시
self.agent_hook.periodic_check(self.positions)    # 4시간 주기
self.agent_hook.on_position_close(pos, reason, pnl)  # 청산 시 (자동 반성 포함)
```

### 신규 연동 (진입 전 리스크 체크)
```python
# auto_trader_v9.py의 try_enter() 직전에 추가
result = self.agent_hook.risk_check(
    signal=signal_dict,
    positions=self.positions,
    market_data={
        "btc_price": btc_price,
        "btc_level": btc_level,
        "btc_atr_pct": btc_atr,
        "fear_greed": fgi,
        "funding_rate": funding,
    },
    account_balance=self.balance,
    recent_losses=self.consecutive_losses,
)
if result and not result["approved"]:
    print(f"  [리스크 거부] {result['risk_level']}: {result['brain_advice']}")
    return  # 진입 스킵
# 레버리지 조정
leverage = result["recommended_leverage"] if result else 20
```

### 데이터 흐름 (Phase A+B+C+D 통합)
```
┌─────────────────────────────────────────────────────────┐
│  auto_trader_v9.py (기존)                               │
│  - convergence_strategy.py → signal 생성                │
│  - BTC 필터 / CoinGlass / exchange                      │
└───────────────┬─────────────────────────────────────────┘
                │ signal + positions + market_data
                ▼
┌─────────────────────────────────────────────────────────┐
│  EnrichedStateBuilder (Phase D)                         │
│  - Vision AI 피처 (선택)                                │
│  - RAG 컨텍스트 (선택)                                  │
│  - Brain 자문 (Phase C)                                 │
│  → PipelineContext                                      │
└───────────────┬─────────────────────────────────────────┘
                │ PipelineContext
                ▼
┌─────────────────────────────────────────────────────────┐
│  RiskPipeline (Phase B)                                 │
│  Analyst → Risk → Hedge → Leverage                      │
│  → RiskDecision                                         │
└───────────────┬─────────────────────────────────────────┘
                │ approved? + leverage + hedge
                ▼
         진입 / 거부 / 레버리지 조정

              (청산 시)
                ▼
┌─────────────────────────────────────────────────────────┐
│  ReflectionEngine (Phase C)                             │
│  → 규칙 생성/갱신                                       │
│  → Brain 승률 업데이트                                  │
│  → 텔레그램 학습 보고                                   │
└─────────────────────────────────────────────────────────┘
```

---

## Phase E: LLM API 최적화 + Walk-Forward 프레임워크 (완료)

### 7. LLM API 최적화

| 파일 | 변경 내용 | 상태 |
|------|----------|------|
| `agents/core/base.py` | 글로벌 비용 추적 싱글턴 + 프롬프트 캐싱 + 모델별/에이전트별 집계 | 완료 |
| `risk/risk_agent.py` | LLM 호출 `deep=True` → `deep=False` (Sonnet → Haiku) | 완료 |
| `agents/core/orchestrator.py` | Bull/Bear 토론 DEEP→QUICK 모델 + 비용 추적 import + 캐싱 | 완료 |

#### 비용 추적 시스템
```python
_usage_stats = {
    "calls": 0,           # 총 API 호출 수
    "input_tokens": 0,    # 입력 토큰 누적
    "output_tokens": 0,   # 출력 토큰 누적
    "cache_read_tokens": 0,   # 캐시 읽기 토큰
    "cache_write_tokens": 0,  # 캐시 쓰기 토큰
    "estimated_cost_usd": 0.0, # 추정 비용 (USD)
    "errors": 0,          # 에러 횟수
    "by_model": {...},    # 모델별 집계
    "by_agent": {...},    # 에이전트별 집계
}
```

#### 프롬프트 캐싱
- 모든 시스템 프롬프트에 `cache_control: {"type": "ephemeral"}` 적용
- 캐시 읽기: 비용 10%, 캐시 쓰기: 비용 125%
- `cache_system` 파라미터로 제어 (기본 True)

#### 모델 배분 최적화

| 호출처 | 변경 전 | 변경 후 | 절감 |
|--------|---------|---------|------|
| Bull/Bear 토론 (2회) | Sonnet | Haiku | ~73% |
| RiskAgent LLM 분석 | Sonnet | Haiku | ~73% |
| HedgeAgent LLM 분석 | Sonnet | Haiku | ~73% |
| Moderator 토론 정리 | Sonnet | Sonnet 유지 | 0% (품질 유지) |
| AnalystAgent 분석 | Sonnet | Sonnet 유지 | 0% (품질 유지) |

**예상 비용 절감: ~33%** (시뮬레이션 기반: $9.45 → $6.37 per 500 calls)

### 8. Walk-Forward 백테스트 프레임워크

| 파일 | 역할 | 상태 |
|------|------|------|
| `backtests/walk_forward.py` | 3윈도우 Walk-Forward 검증 + Baseline/Enhanced/Memory 비교 | 완료 |

#### 윈도우 설정
```
Window 1: Train 2024-07~2025-03 → Val 2025-04~06 → Test 2025-07~09
Window 2: Train 2024-10~2025-06 → Val 2025-07~09 → Test 2025-10~12
Window 3: Train 2025-01~2025-09 → Val 2025-10~12 → Test 2026-01~03
```

#### 평가 모드 3종
1. **Baseline**: 고정 SL 기반 PF/승률/SL히트율 계산
2. **Enhanced**: RiskPipeline 필터 → 레버리지 조정/시그널 거부 → PF 개선 측정
3. **Memory Enhanced**: Brain+Reflection 학습 루프 → train 학습 후 test 적용

#### CLI
```bash
python backtests/walk_forward.py --data signals.csv --level production --output report.md
```

### Phase E 테스트 결과 (tests/test_phase_e.py)

실행 일시: 2026-04-19

**총 7 함수 (36 서브테스트): PASS 36, FAIL 0**

| 카테고리 | PASS | 주요 검증 항목 |
|----------|------|----------------|
| 비용 추적기 | 10 | 초기화, 호출수, 토큰, 캐시, 비용, 모델별, 에이전트별, 다중호출, 비용맵, reset |
| 프롬프트 캐싱 | 3 | cache_system 파라미터, 기본값 True, llm_json 전달 |
| 모델 배분 | 6 | QUICK/DEEP 모델명, RiskAgent Haiku, HedgeAgent Haiku, 오케스트레이터 Haiku, 추적 import |
| 파이프라인+추적 | 3 | 파이프라인 실행, LLM 추적, API 없이 폴백 |
| WF 프레임워크 | 7 | 3윈도우, Train<Val<Test 순서, 시그널 레벨 4종, 프로덕션 설정, 레버리지 20 |
| WF 미니 실행 | 5 | 데이터 로드, 필터, baseline PF, enhanced PF, 리포트 생성 |
| 비용 효율 | 2 | 33% 절감 시뮬레이션, Haiku<Sonnet 비용 |

---

## Phase F: 3-Cycle 코드 리뷰 결함 수정 (완료)

> 구현일: 2026-04-20
> 커밋: `f84e8fc`

### 배경

Phase A~E 완료 후 전체 코드 3-cycle 리뷰 (전체→중분류→소분류→기초 × 3회) 실시.
7 CRITICAL, 8 HIGH, 6 MEDIUM 이슈 발견. Phase F에서 CRITICAL + HIGH 전체 수정.

### 발견된 핵심 문제

**가장 심각한 발견**: Phase B~E 리스크 파이프라인이 완전 구현되었으나, `auto_trader_v9.py`의 `try_enter()`에서 **한 번도 호출되지 않았음**. `risk_check()` 메서드가 정의만 되고 실제 진입 흐름에 연결되지 않은 상태.

### 수정 내역 (10건, 8개 파일)

#### CRITICAL 수정

| 코드 | 파일 | 수정 내용 |
|------|------|----------|
| **C1** | `auto_trader_v9.py` | `risk_check()`을 `try_enter()`에 연결 — BTC 필터 이후, SL 계산 이전에 삽입. high/critical 시 진입 차단 |
| **C2/C3** | `auto_trader_v9.py` | 시그널 dict에 `gap`, `adx` 최상위 필드 추가 — `details.gap_pct`/`adx_value` 외에 Memory/Brain이 읽는 필드 통일 |
| **C4** | `agents/memory/rule_store.py` | `threading.Lock` 추가 + atomic write (`tmp → os.replace`). add/update/record_hit/record_miss 모두 잠금 |
| **C5** | `risk/risk_agent.py` | 청산거리 계산에서 `max(1, pos.leverage)` 방어 — leverage=0 시 ZeroDivisionError 방지 |
| **C7** | `risk/leverage_agent.py` | ATR→레버리지 매핑: 단계 함수 → **선형 보간** 변환. ATR 1.0 경계에서 20x→15x 급변 제거 |

#### HIGH 수정

| 코드 | 파일 | 수정 내용 |
|------|------|----------|
| **H1** | `risk/risk_agent.py` | `_safe_risk_level()` 함수 추가 — LLM이 "HIGH", "Medium" 등 대소문자 불일치 출력 시 `.lower().strip()` 정규화 + 잘못된 값 → MEDIUM 폴백 |
| **H5** | `agents/core/base.py` | 캐시 비용 공식 수정: `(inp - cache_read)` → `(inp - cache_read - cache_write)`. cache_write 토큰이 inp에 이미 포함되어 이중 계산됨 |

#### 추가 수정

| 항목 | 파일 | 수정 내용 |
|------|------|----------|
| Pattern 3 타임아웃 | `agents/memory/reflection_engine.py` | 패턴 3 (96캔들+ 타임아웃) 감지 시 규칙 생성 누락 → `rule_store.add()` 추가 |
| _entry_snapshots Lock | `agents/v9_hook.py` | `_snapshot_lock` 추가 — `risk_check()` (메인 스레드)와 `_reflect_on_close()` (daemon 스레드) 간 경쟁 방지 |
| gap 필드 폴백 | `agents/memory/trading_brain.py` | `signal.get("gap")` → `gap \|\| gap_pct \|\| details.gap_pct` 순차 탐색. 기존에는 항상 0 반환 |
| test_result 리네임 | `tests/test_phase_a~d.py` | `test_result()` → `_test_result()` — pytest가 fixture로 오인식하여 전체 테스트 실패 |

### C1 상세: risk_check() 연결 (핵심)

```python
# auto_trader_v9.py try_enter() 내부 (BTC 필터 이후, SL 계산 이전)
if self.agent_hook is not None:
    risk_result = self.agent_hook.risk_check(
        signal=signal,
        positions=[p.__dict__ for p in self.positions],
        account_balance=self.executor.total_balance(),
    )
    if risk_result and not risk_result.get("approved", True):
        print(f"  🛑 리스크 차단: {sym} {d} — 레벨: {risk_result['risk_level']}")
        return
```

**안전 설계**:
- `agent_hook`이 None이면 스킵 (에이전트 미초기화 시)
- `risk_check()` 자체 예외 시 `try/except`로 잡고 진입 계속 (봇 중단 방지)
- `risk_result`가 None이면 스킵 (리스크 파이프라인 비활성화 시)
- `approved` 키 없으면 기본 True (안전한 방향)

### C7 상세: ATR→레버리지 선형 보간

```
기존 (단계 함수):
  ATR 0.99% → 20x
  ATR 1.01% → 15x  ← 급변! (0.02% 차이로 5x 변동)

수정 (선형 보간):
  ATR 0.5% → 20x
  ATR 1.0% → 20x
  ATR 1.5% → 18x  ← 연속적
  ATR 2.0% → 15x
  ATR 2.5% → 14x  ← 연속적
  ATR 3.0% → 12x
```

### H5 상세: 캐시 비용 공식 수정

```
Anthropic API에서 cache_write 토큰은 input_tokens에 포함됨.

기존 (잘못된):
  effective = (inp - cache_read) × 1.0 + cache_read × 0.1 + cache_write × 1.25
  → cache_write가 (inp - cache_read)에도 포함 + 별도 1.25배 → 이중 계산

수정:
  effective = (inp - cache_read - cache_write) × 1.0 + cache_read × 0.1 + cache_write × 1.25
  → cache_write를 일반 입력에서 제외 후 125% 요율 적용
```

### Phase F 테스트 결과 (tests/test_phase_f.py)

실행 일시: 2026-04-20

**총 7 함수: PASS 7, FAIL 0**

| 테스트 | 결과 | 검증 항목 |
|--------|------|----------|
| test_safe_risk_level | PASS | "HIGH"/"Medium"/" low " → 정상 변환, "unknown" → MEDIUM 폴백 |
| test_atr_leverage_interpolation | PASS | ATR 0.5→20x, 1.5→18x, 2.5→14x, 인접값 차이 ≤3 (연속성) |
| test_cache_cost_formula | PASS | cache_write 이중 계산 제거 확인 |
| test_rule_store_thread_safety | PASS | 4스레드 × 5규칙 = 20건 동시 저장, JSON 무결성 확인 |
| test_leverage_zero_defense | PASS | leverage=0 → ZeroDivisionError 없이 정상 평가 |
| test_trading_brain_gap_fallback | PASS | gap / gap_pct / details.gap_pct 3가지 형태 모두 정상 |
| test_timeout_pattern_creates_rule | PASS | 96캔들+ 타임아웃 시 규칙 1건 생성 |

---

## 누적 테스트 요약 (최종)

| Phase | 총 | PASS | FAIL | 비고 |
|-------|-----|------|------|------|
| A (RAG + Vision) | 9 | 9 | 0 | test_result 리네임 후 정상 |
| B (리스크 에이전트) | 8 | 8 | 0 | |
| C (적응형 메모리) | 5 | 5 | 0 | |
| D (통합 레이어) | 8 | 8 | 0 | |
| E (LLM 최적화 + WF) | 7 | 7 | 0 | |
| F (결함 수정) | 7 | 7 | 0 | |
| **합계** | **44** | **44** | **0** | |

---

## 다음 단계

1. **실데이터 Walk-Forward 실행** — GCE/Windows 서버에서 `signals_all_labeled.csv` (370만 행) 투입 → Baseline vs Enhanced vs Memory PF 비교
2. **LLM API 키 적용** — 실제 Claude Haiku 호출 → 반성 품질 평가 (규칙 기반 vs LLM 비교)
3. **Shadow Mode 운영** — `risk_check()` 연결 완료 → 실제 개입 효과 측정 (Shadow Mode에서 approved/rejected 비율 로깅)
4. **Phase 4 RL 연계** — `rl/agent/sl_agent.py`에 EnrichedStateBuilder의 피처를 state로 주입
5. **MEDIUM 이슈 후속 처리** — 멀티프로세싱 LLM 비용 추적 격리, MarketState ATR 기본값 보수화 검토
