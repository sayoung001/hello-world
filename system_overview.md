# EMA 수렴 브레이크아웃 자동매매 시스템 — 전체 구조 문서

> **목적**: 이 문서는 현재 운영 중인 자동매매 시스템의 전체 아키텍처, 에이전트, 전략 로직, 데이터 소스, 리스크 관리를 상세히 기술합니다.
> 코드 파일을 직접 전달하지 않고도 시스템을 완전히 이해할 수 있도록 작성되었습니다.
>
> **최종 업데이트**: 2026-06-10

---

## 목차

1. [시스템 개요](#1-시스템-개요)
2. [핵심 전략: EMA 수렴 브레이크아웃](#2-핵심-전략-ema-수렴-브레이크아웃)
3. [Bot1: ConvergenceTrader (프로덕션 메인봇)](#3-bot1-convergencetrader-프로덕션-메인봇)
4. [Bot2: HuntTrader (BTC 돌파 추격봇)](#4-bot2-hunttrader-btc-돌파-추격봇)
5. [에이전트 시스템 아키텍처](#5-에이전트-시스템-아키텍처)
6. [Context Layer 에이전트 (Agent 1~4, 6)](#6-context-layer-에이전트-agent-14-6)
7. [Position Layer 에이전트 (Agent 5)](#7-position-layer-에이전트-agent-5)
8. [오케스트레이터: 토론 + 합의 시스템](#8-오케스트레이터-토론--합의-시스템)
9. [독립 분석 에이전트](#9-독립-분석-에이전트)
10. [실시간 감지 시스템](#10-실시간-감지-시스템)
11. [리스크 관리 파이프라인](#11-리스크-관리-파이프라인)
12. [적응형 메모리 시스템](#12-적응형-메모리-시스템)
13. [데이터 소스](#13-데이터-소스)
14. [텔레그램 명령어 체계](#14-텔레그램-명령어-체계)
15. [출력 시스템](#15-출력-시스템)
16. [현재 성과 및 발견된 문제점](#16-현재-성과-및-발견된-문제점)
17. [향후 계획 (RL + LLM)](#17-향후-계획-rl--llm)
18. [파일 구조 맵](#18-파일-구조-맵)

---

## 1. 시스템 개요

### 한 줄 요약
바이낸스 선물(USDT-M) 20x 레버리지 자동매매 봇. EMA 12/26 수렴(스퀴즈) 감지 후 브레이크아웃 시그널 기반으로 진입하며, LLM 에이전트 6개가 시장 분석 + 포지션 판결을 수행하는 하이브리드 시스템.

### 듀얼 봇 운영
| 구분 | Bot1 (ConvergenceTrader) | Bot2 (HuntTrader) |
|------|--------------------------|-------------------|
| **역할** | BTC 횡보 시 스퀴즈 이탈 수익 | BTC 돌파 시 알트코인 추격 |
| **전략** | EMA 수렴 → 브레이크아웃 | BTC 돌파 감지 → 알트 모멘텀 |
| **모드** | E (Cascade 즉시청산) / D (CasTrail 트레일링) | Hunt (BTC 방향 추종) |
| **워치리스트** | 22개 고정 코인 | 바이낸스 전체 자동 구성 (10M+ 거래량) |
| **계정** | 독립 API 키 | 독립 API 키 |

### 운영 환경
- **거래소**: Binance Futures (USDT-M)
- **레버리지**: 20x (고정)
- **캔들**: 15분봉 기반 (모든 시간 단위는 캔들 수로 표현)
- **서버**: Windows, Python 3.10+, 24+ CPU 코어
- **LLM**: Anthropic Claude (Haiku=빠른분석, Sonnet=심층분석)

---

## 2. 핵심 전략: EMA 수렴 브레이크아웃

### 전략 원리
EMA 12와 EMA 26이 가격 대비 0.2% 이내로 수렴(스퀴즈)한 상태가 일정 기간 지속된 후, 가격이 이 밴드를 이탈(브레이크아웃)하는 순간을 포착하여 진입합니다.

### 시그널 생성 흐름 (`convergence_strategy.py`)

```
[15분봉 200개 로드]
    ↓
[지표 계산: EMA12, EMA26, ATR, ADX, 볼린저밴드, RSI, MACD]
    ↓
[스퀴즈 감지] ← |EMA12 - EMA26| / price ≤ 0.2% 연속 N캔들
    ↓
[브레이크아웃 감지] ← ATR 1.5배 이상 이동 + 거래량 1.5배 이상 + 볼린저 밴드 이탈
    ↓
[시그널 구성] ← 방향(LONG/SHORT) + 신뢰도 + SL/TP + 이유
```

### 스퀴즈 감지 (`_detect_squeeze`)
- **조건**: `|EMA12 - EMA26| / price ≤ 0.2%`가 연속으로 유지
- **최소 캔들**: 30개 (Bot1 기본), 6개 (Bot2 완화)
- **최대 캔들**: 40개 (너무 오래된 스퀴즈는 무효)
- **추가 조건**: ADX ≥ 15 (최소 추세 강도)
- **출력**: squeeze_candles, squeeze_high, squeeze_low, squeeze_mid, BBW 스퀴즈 여부

### 브레이크아웃 감지 (`_detect_breakout`)
- **가격 이동**: `|close - prev_close| / ATR ≥ 1.5` (ATR 1.5배 이상)
- **거래량**: 20캔들 평균 대비 1.5배 이상
- **볼린저 밴드 이탈**: 종가가 밴드 밖에 위치
- **방향 판별**: close > squeeze_high → LONG, close < squeeze_low → SHORT
- **MACD 정렬**: MACD와 방향 일치 시 추가 확인

### 신뢰도(Confidence) 채점 (12점 만점 → 0~100%)
| 항목 | 조건 | 점수 |
|------|------|------|
| 스퀴즈 기간 | ≥20캔들 +3, ≥10캔들 +2, 그외 +1 | 1~3 |
| BBW 스퀴즈 | 볼린저밴드 폭 < 평균 70% | +2 |
| 브레이크아웃 강도 | ≥2.0 ATR +2, ≥1.5 ATR +1 | 1~2 |
| 거래량 폭발 | ≥2.0배 +2, ≥1.3배 +1 | 1~2 |
| MACD 정렬 | 방향 일치 | +1 |
| ADX | > 25 | +1 |

### SL/TP 계산 (현재 — **고정 방식, 핵심 문제**)
```
LONG:
  SL = squeeze_low - (squeeze_range × 0.3 × ATR)    ← 스퀴즈 저점 아래
  TP = squeeze_high + (squeeze_range × 2.618)         ← 피보나치 확장

SHORT:
  SL = squeeze_high + (squeeze_range × 0.3 × ATR)   ← 스퀴즈 고점 위
  TP = squeeze_low - (squeeze_range × 2.618)          ← 피보나치 확장
```

### 헤지 브레이크아웃 (HedgeBreakout)
- 대안 전략으로 구현됨: 스퀴즈 구간에서 롱+숏 동시 진입
- 이기는 쪽 TP가 지는 쪽 SL보다 넓어 순수익 구조
- 현재 프로덕션 미사용

---

## 3. Bot1: ConvergenceTrader (프로덕션 메인봇)

### 모드 설정

#### E 모드 (Cascade — 수익 극대화)
```
Lock Fibo 2.0 → SL이 본전으로 이동 → TP Fibo 8.0 (고정 목표)
EE(조기청산): 4~6시간 후 ROE ≤ -1.0%면 청산
```

#### D 모드 (CasTrail — 안정 추구)
```
Lock Fibo 2.0 → SL이 본전으로 이동 → Trail 50% (최대 수익의 50% 보존)
EE(조기청산): 2~4시간 후 ROE ≤ -0.5%면 청산
```

### 핵심 파라미터
| 항목 | Bot1 값 |
|------|---------|
| 워치리스트 | SOL, XRP, NEAR, UNI, COMP, SAND, QNT, ANKR, SUI, NTRN, TRX, ENJ, WLD, 1000PEPE, DOGE, ETH, BNB, DOT, AVAX, BCH, AXS, POLYX (22개) |
| 레버리지 | 20x |
| 리스크/트레이드 | 10% |
| 최대 포지션 수 | 7 |
| 마진 노출 한도 | 50% |
| 최소 신뢰도 | 70 |
| 최소 ADX | 25 |
| 최소 스퀴즈 캔들 | 15 |
| 최소 거래량 배율 | 1.5x |
| 타임아웃 | 192시간 |
| 수수료 | 0.04% |

### BTC 7단계 필터 (`BTCTrend7Level`)

BTC 15분봉 기준으로 시장 상태를 7단계로 분류합니다.

| 레벨 | 상태 | 조건 | 진입 제한 |
|------|------|------|-----------|
| +3 | STRONG_BULL | EMA20>60, ADX>30, RSI>60, 1h수익>+1% | **숏 진입 차단** |
| +2 | BULL | EMA20>60, ADX>20 | 없음 |
| +1 | MILD_BULL | EMA20>60, ADX≤20 | 없음 |
| 0 | NEUTRAL | — | 없음 |
| -1 | MILD_BEAR | EMA20<60, ADX≤20 | 없음 |
| -2 | BEAR | EMA20<60, ADX>20 | 없음 |
| -3 | CRASH | EMA20<60, ADX>30, RSI<40, 1h수익<-1% | **롱 진입 차단** |

V9.3 최소 필터: CRASH(-3)만 롱 차단, STRONG_BULL(+3)만 숏 차단 (나머지는 자유 진입)

### 포지션 관리 흐름 (`manage_positions`)
```
15초마다 실행:
  1. SL 히트 확인 → 청산
  2. TP 히트 확인 → 청산
  3. Lock 트리거 (F2.0): max_fav_roe ≥ lock_trigger
     → SL을 entry_price(본전)로 이동
     → 안전 순서: 새 SL 주문 먼저 → 이전 SL 취소 (gap 방지)
  4. Trail (D모드 전용): sl_locked 상태에서 max_fav_roe > 0.5%
     → 현재 ROE ≤ max_fav_roe × 50% 시 청산
  5. EE (조기청산): 설정 시간 경과 후 ROE 기준 미달 시 청산
  6. 타임아웃: 192시간 초과 시 강제 청산
```

### 메인 루프 (`run`) — 15초 사이클
```
매 사이클:
  ✓ BTC 트렌드 업데이트 (60초 캐시)
  ✓ 텔레그램 명령어 확인
  ✓ 포지션 관리 (15초)
  ✓ 시그널 스캔 (15분 경계 또는 60초)
  ✓ 신규 진입 시도
  ✓ 상태 리포트 (2시간)
  ✓ 시장 분석 (4시간)
  ✓ 돌파 전문가 스캔 (4시간, 조정 가능)
  ✓ 스퀴즈 감시 모니터링 (1분)
  ✓ SL/TP 검증 (30분)
  ✓ 거래소 동기화 (5분)
  ✓ 일일 리셋 (00:00 KST)
```

### 거래소 동기화 (`runtime_sync`)
5분마다 바이낸스 실제 포지션과 봇 내부 상태를 비교합니다:
- SL/TP 자동 체결 감지 (거래소에서 자동 청산된 경우)
- 수동 진입 감지 (봇 외부에서 열린 포지션 → 봇 관리에 추가)
- 기존 포지션 비율 참조하여 SL/TP 자동 설정

### 진입 흐름 (`try_enter`)
```
시그널 수신
  → 포지션 제한 확인 (최대 7개, 코인 중복 불가, 일 10회 제한)
  → BTC 필터 확인 (CRASH시 롱 차단, STRONG_BULL시 숏 차단)
  → 에이전트 리스크 체크 (Phase F)
  → SL/TP 계산 (스퀴즈 범위 기반)
  → 사이징: (리스크% / SL거리%) × 잔고 × 복리 계수
  → 시장가 진입 + SL/TP 주문 설정
  → 텔레그램 알림 + 에이전트 스냅샷 저장
```

---

## 4. Bot2: HuntTrader (BTC 돌파 추격봇)

### 설계 철학
Bot1이 BTC 횡보 시 수익을 내는 반면, Bot2는 BTC가 강하게 움직일 때 알트코인 추격으로 수익을 냅니다. 두 봇은 상호 보완적입니다.

### BTCBreakoutTracker 상태 머신
```
STANDBY ──[BTC 스퀴즈 이탈]──→ TRIGGER ──→ HUNT ──[3~4시간]──→ COOLDOWN ──[1시간]──→ STANDBY
```

**트리거 조건 (OR)**:
1. EMA 스퀴즈 → 비스퀴즈 전환 (gap 0.5% → 초과)
2. Gap ≥ 0.5% (BTC_TRIGGER_GAP)
3. Gap 가속도 ≥ 0.15% per cycle

### Bot2 완화된 필터 (vs Bot1)
| 항목 | Bot1 | Bot2 |
|------|------|------|
| 신뢰도 | 70 | 50 |
| ADX | 25 | 18 |
| 스퀴즈 캔들 | 15 | 6 |
| 거래량 배율 | 1.5x | 1.3x |
| Vol Surge | 1.0x | 1.5x |
| 리스크/트레이드 | 10% | 8% |
| 최대 포지션 | 7 | 5 |

### 모멘텀 시그널 (Bot2 전용)
스퀴즈 없이도 강한 모멘텀이 감지되면 진입합니다:
```
조건:
  - ATR 이동 ≥ 2.0× ATR
  - 거래량 ≥ 2.5× 평균
  - ADX ≥ 18
  - RSI: LONG > 55, SHORT < 45

SL = price ± 1.5 × ATR
TP = price ± 3.0 × ATR
```

### 스캔 로직
1. HUNT 상태일 때만 스캔 실행
2. 먼저 convergence detect() 시도 (신뢰도 ≥ 50)
3. 실패 시 모멘텀 detect() 시도
4. **BTC 방향 필터**: 시그널 방향 = hunt_tracker 방향일 때만 진입

---

## 5. 에이전트 시스템 아키텍처

### 전체 구조
```
┌─────────────────────────────────────────────────────────────┐
│                    V9 Hook (v9_hook.py)                       │
│    ┌──────────┐  ┌──────────┐  ┌───────────────┐            │
│    │ 정기분석  │  │ 긴급분석  │  │ 포지션 오픈   │            │
│    │ (4시간)   │  │ (급락시)  │  │ (진입 시)     │            │
│    └────┬─────┘  └────┬─────┘  └───────┬───────┘            │
│         │              │                │                     │
│         ▼              ▼                ▼                     │
│  ┌─────────────────────────────────────────────────────┐     │
│  │              Orchestrator (토론 시스템)               │     │
│  │                                                       │     │
│  │  Phase 1: Context Layer                               │     │
│  │  ┌───────┬──────┬──────┬──────┬──────┐               │     │
│  │  │Agent1 │Agent2│Agent3│Agent4│Agent6│               │     │
│  │  │BTC구조│매크로│상관관│알트  │뉴스  │               │     │
│  │  └───┬───┴──┬───┴──┬───┴──┬───┴──┬───┘               │     │
│  │      └──────┴──────┴──────┴──────┘                    │     │
│  │                    ↓                                   │     │
│  │  Phase 2a: Bull/Bear 토론 (LLM Haiku 2회)            │     │
│  │  Phase 2b: 동적 가중치 (regime별 Factual/Subjective)  │     │
│  │  Phase 2c: Moderator (LLM Sonnet 1회)                │     │
│  │                    ↓                                   │     │
│  │  Phase 3: Position Layer                              │     │
│  │  ┌───────┐                                            │     │
│  │  │Agent5 │ → 포지션별 HOLD/REDUCE/CLOSE 판결         │     │
│  │  │포지션 │                                            │     │
│  │  │심판   │                                            │     │
│  │  └───────┘                                            │     │
│  └─────────────────────────────────────────────────────┘     │
│                                                               │
│  ┌────────────────────────────────────────────┐              │
│  │         독립 분석 에이전트                    │              │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  │              │
│  │  │알트분석가│  │돌파전문가│  │스퀴즈감지│  │              │
│  │  │(LLM)    │  │(LLM)    │  │(규칙기반)│  │              │
│  │  └──────────┘  └──────────┘  └──────────┘  │              │
│  └────────────────────────────────────────────┘              │
│                                                               │
│  ┌────────────────────────────────────────────┐              │
│  │         실시간 감지 (15초 폴링)              │              │
│  │  ┌──────────┐  ┌──────────────┐             │              │
│  │  │급락감지  │  │추세반전감지  │             │              │
│  │  │(4레벨)   │  │(EMA 교차)    │             │              │
│  │  └──────────┘  └──────────────┘             │              │
│  └────────────────────────────────────────────┘              │
│                                                               │
│  ┌────────────────────────────────────────────┐              │
│  │         리스크 파이프라인 (Phase B)           │              │
│  │  Analyst → Risk → Hedge → Leverage           │              │
│  └────────────────────────────────────────────┘              │
│                                                               │
│  ┌────────────────────────────────────────────┐              │
│  │         적응형 메모리 (Phase C)               │              │
│  │  TradingBrain + RuleStore + ReflectionEngine │              │
│  └────────────────────────────────────────────┘              │
└─────────────────────────────────────────────────────────────┘
```

### AgentBase 기반 클래스
모든 에이전트의 공통 기반입니다:
- **Quick LLM (Haiku)**: 데이터 수집 시 사용 — 빠르고 저렴
- **Deep LLM (Sonnet)**: 심층 분석 시 사용 — 정확하지만 비쌈
- **파이프라인**: `collect_data()` → `analyze()` → `AgentMessage`
- **프롬프트 캐싱**: 시스템 프롬프트 캐시로 토큰 비용 절감
- **사용량 추적**: 모델/에이전트별 토큰 소모량 및 비용 글로벌 추적

### 에이전트 유형 분류
| 유형 | 에이전트 | 특성 |
|------|----------|------|
| **Factual** (객관적) | BTC구조, 상관관계 | 시장 데이터 기반 사실 분석 — risk-off 시 가중치 높음 |
| **Subjective** (주관적) | 매크로, 알트생태계, 뉴스 | 해석이 필요한 분석 — risk-on 시 가중치 상승 |
| **Position** (판결) | 포지션심판 | 개별 포지션 HOLD/CLOSE 판단 |
| **독립** | 알트분석가, 돌파전문가, 스퀴즈감지 | 오케스트레이터 외부에서 독립 실행 |

---

## 6. Context Layer 에이전트 (Agent 1~4, 6)

### Agent 1: BTC 구조 분석가 (`btc_structure.py`)
**유형**: Factual | **LLM**: Sonnet (심층)

**수집 데이터**:
- 바이낸스: 15m/1h/4h OHLCV (100캔들씩), EMA12/26, RSI, ATR
- Volume Profile: POC, HVN, LVN, Value Area (50캔들, 30빈)
- CoinGlass: OI 변화, 펀딩레이트, 롱숏비율, 테이커 압력, 청산 클러스터, 공포/탐욕 지수
- BTCFilter: 7단계 시장 상태 + 안전 플래그
- 패턴 감지: VA 돌파, HVN 거부, 스퀴즈→확장, 모멘텀 캔들

**분석 출력**:
- BTC 현재가, 핵심 저항/지지, Volume Profile POC
- 브레이크아웃 확률 (0~1)
- 트렌드 (bullish/neutral/bearish) + 강도
- 청산 중력 방향 (UP/DOWN/NEUTRAL)
- 리스크 레벨 (SAFE/CAUTION/DANGER)

**폴백 규칙**:
- EMA12 > EMA26 → bullish (55% 브레이크아웃), 역 → bearish (35%)
- BTCFilter BEAR/CRASH → -15%, BULL → +10%
- danger_level ≥ 2 또는 (gravity ≥ 7 AND DOWN) → DANGER

---

### Agent 2: 매크로 분석가 (`macro.py`)
**유형**: Subjective | **LLM**: Sonnet (심층)

**수집 데이터**:
- 경제 이벤트 캘린더: FOMC, CPI, NFP, ISM PMI, PCE, GDP (하드코딩 + 반복 일정)
- DXY 프록시: BTC 7일 변화로 추정 (BTC -5% → DXY 강세)
- 실현 변동성: 30일 수익률 표준편차 × √365 (연율화)
- VIX 매핑: BTC 변동성을 전통시장 VIX 수준으로 변환
- 금 프록시: PAXG/USDT 7일 변화 (안전자산 수요 판별)
- 시장 세션: 평일/주말, 미국장 시간, 월/금 특수 상황

**리스크 식욕 채점 (-12 ~ +12)**:
| 요소 | 범위 | 기준 |
|------|------|------|
| 공포/탐욕 | ±3 | FGI ≥75 → +3, ≤25 → -3 |
| VIX | -3~+2 | extreme → -3, low → +2 |
| DXY | ±2 | bearish → +2, bullish → -2 |
| 고영향 이벤트 | -3~0 | ≥2개 → -3, 1개 → -1 |
| 금 | -2~+1 | 안전자산 수요 → -2 |
| 주말 | -1~0 | 주말 → -1 (유동성 부족) |

- score ≤ -5 → DANGER, ≤ -2 또는 고영향 이벤트 → CAUTION, 그 외 → SAFE
- 신뢰도: 0.4 + |score| × 0.05 (최대 0.85)

---

### Agent 3: 상관관계 분석가 (`correlation.py`)
**유형**: Factual | **LLM**: Sonnet (심층)

**분석 코인**: BTC, ETH, SOL, BNB, XRP, DOGE, AVAX, LINK, SUI

**핵심 분석**:
1. **롤링 상관계수**: 24h, 72h, 7d 윈도우에서 BTC-알트 수익률 상관관계
2. **BTC/ETH 비율 추세**: 상승 = BTC 도미넌스 증가, 하락 = 도미넌스 감소
3. **알트 동조화**: 7일 상관관계 평균 → [0, 1] 정규화
4. **레짐 변화 감지**: 
   - ≥ 0.7: "high_sync" (시장 일방향)
   - 0.4~0.7: "normal"
   - 0.1~0.4: "decorrelating" (분산)
   - < 0.1: "breakdown" (탈동조)
5. **전환 감지**: recent - earlier > 0.2 → "분산→동조 회복", < -0.2 → "동조→분산 전환"

**리스크 매핑**:
- 동조화 < 0.2 → DANGER
- 0.2~0.4 → CAUTION
- ≥ 0.4 → SAFE

---

### Agent 4: 알트 생태계 분석가 (`alt_ecosystem.py`)
**유형**: Subjective | **LLM**: Sonnet (심층)

**섹터 분류**:
| 섹터 | 코인 |
|------|------|
| L1 | SUI, SOL, AVAX, APT, SEI, TIA |
| AI | WLD, FET, RENDER, TAO |
| Meme | DOGE, PEPE, WIF, BONK, FLOKI |
| DeFi | UNI, AAVE, MKR, CRV |
| Infra | LINK, DOT, ATOM, ARB, OP |
| Payment/Exchange | XRP, BNB |
| Legacy | LTC |

**핵심 분석**:
1. **분산도 (Dispersion)**: 방향분산 60% + 크기분산(CV) 40%
   - 시장 합의: positive > 70% → bullish, negative > 70% → bearish
2. **섹터 모멘텀**: 4h, 24h, 7d 다중 타임프레임 수익률
   - 선도/후행 섹터 식별
3. **코인 베타**: β = Cov(coin,BTC)/Var(BTC), 72h 윈도우
   - β > 2.0 → 극단 변동성 (+10 리스크 점수)
4. **알트코인 시즌 인덱스**: 알트가 BTC 대비 아웃퍼폼하는 비율
   - ≥ 75%: "alt_season" 🚀
   - 50~75%: "alt_leaning"
   - 25~50%: "neutral"
   - < 25%: "btc_season" ₿
5. **도미넌스 추세**: BTC 24h 변화 - 알트 평균 24h 변화

---

### Agent 6: 뉴스/이슈 분석가 (`news_sentiment.py`)
**유형**: Subjective | **LLM**: Sonnet (심층)

**데이터 소스**:
- CryptoCompare News API (무료)
- Alternative.me Fear/Greed Index (무료)
- LLM 지식베이스 (최신 이슈)

**이슈 카테고리 & 가중치**:
| 카테고리 | 가중치 | 키워드 | 영향 |
|----------|--------|--------|------|
| 지정학 | 3.0 | war, military, iran, missile, nuclear, sanction, tariff | 치명적 |
| 규제 | 2.5 | sec, ban, lawsuit, enforcement, cftc, doj | 고 |
| 매크로 쇼크 | 2.0 | fed, fomc, cpi, inflation, recession, bank failure | 고 |
| 거래소 리스크 | 2.5 | hack, rug pull, insolvency, bankrupt, freeze | 고 |
| 채택 | 1.5 | etf, approval, institutional, blackrock, partnership | 긍정 |
| 웨일 이동 | 1.5 | whale, large transfer, mt gox, unlock, vesting | 정보 |

**감성 점수 합성**:
- 뉴스 데이터 있을 때: 70% 뉴스 감성 + 30% FGI
- 뉴스 없을 때: 80% FGI
- 최근성 가중치: ≤24h = 1.0, 이후 0.3까지 감쇠

**감성 라벨**:
- ≤ -0.6: "panic", -0.6~-0.3: "very_bearish", -0.3~-0.1: "bearish"
- 0.2~0.5: "bullish", ≥ 0.5: "very_bullish", 그 외: "neutral"

---

## 7. Position Layer 에이전트 (Agent 5)

### 포지션 심판 (`position_judge.py`)

시장 합의(MarketConsensus) + 개별 포지션 데이터를 기반으로 각 포지션에 대한 판결을 내립니다.

### 리스크 스코어링 (0~100점)
| 요소 | 가중치 | 세부 기준 |
|------|--------|-----------|
| 청산 거리 | 40% | < 1.5% → 40점, 1.5~3% → 비례, 3~4.5% → 비례, > 4.5% → 0점 |
| 방향 정렬 | 20% | BTC 편향 반대 → +20점, 중립 → +5점 |
| 시장 환경 | 15% | DANGER → 15점, CAUTION → 8점, SAFE → 0점 |
| 보유 시간 | 15% | > 48h → +10점, > 24h → +5점, 숏+손실 → +5점 추가 |
| 코인 베타 | 10% | β > 2.0 → +10점, 1.5~2.0 → +5점 |

### 행동 결정
| 점수 | 행동 | 리스크 |
|------|------|--------|
| ≥ 80 | **CLOSE** (청산) | DANGER |
| ≥ 60 | **REDUCE_SIZE** (감소) | CAUTION |
| < 60 + SL 조정 필요 | **ADJUST_SL** | CAUTION |
| < 60 | **HOLD** (유지) | SAFE |

### SL/TP 조정 추천
- PnL > 1.5% + 2시간 이상 보유 → "본전 이동"
- DANGER + 청산거리 < 4.5% → "타이트닝"
- 패턴 유리 + PnL > 0.5% → TP "확장"
- DANGER + PnL > 0% → TP "축소"

### 최적 보유 시간 추천
- risk_score ≥ 60 → 0시간 (즉시 청산)
- DANGER → 2시간 (높은 긴급도)
- PnL > 2% → 12시간 (SAFE) / 6시간 (CAUTION)
- PnL < -1% → 4시간 (SAFE) / 2시간 (CAUTION)
- 38.4시간 초과 보유 → 타임아웃 경고

### 20x 레버리지 임계값
| 구분 | 가격 이동 | ROE 영향 | 의미 |
|------|-----------|----------|------|
| LIQ_DANGER | 1.5% | -30% | 청산 임박 |
| LIQ_CAUTION | 3.0% | -60% | 주의 필요 |
| LIQ_SAFE | 4.5% | -90% | 안전 여유 |
| 청산 | 5.0% | -100% | 전액 손실 |

---

## 8. 오케스트레이터: 토론 + 합의 시스템

### 실행 파이프라인 (`orchestrator.py`)

```
Phase 1: Context Layer
  → Agent 1~6 순차 실행 → AgentMessage 리스트

Phase 2a: Bull/Bear 토론
  → LLM(Haiku) × 2회 호출 (Bull측 논거 + Bear측 논거)
  → 폴백: 규칙 기반 토론 (리스크/트렌드/감성 신호 분류)

Phase 2b: 동적 가중치 적용
  → regime 추정 → Factual/Subjective 가중치 조정
  → risk-off: Factual 65% / Subjective 35%
  → neutral:  Factual 55% / Subjective 45%
  → risk-on:  Factual 50% / Subjective 50%

Phase 2c: Moderator (합의 도출)
  → LLM(Sonnet) 1회 호출 → MarketConsensus JSON
  → 폴백: 가중 투표 기반 합의

Phase 3: Position Layer
  → Agent 5가 합의 + 포지션 데이터로 개별 판결
```

### Regime 추정
- danger_count ≥ 3 또는 bearish_signals ≥ 4 → "risk-off"
- safe_count ≥ 40% → "risk-on"
- 그 외 → "neutral"
- crash_context level ≥ 2 → 강제 "risk-off"

### 폴백 합의 (LLM 실패 시)
- 가중 투표로 danger_pct, caution_pct 계산
- danger_pct ≥ 45% → DANGER
- caution_pct ≥ 55% → CAUTION
- 그 외 → SAFE

---

## 9. 독립 분석 에이전트

오케스트레이터 파이프라인 외부에서 독립적으로 실행되는 에이전트들입니다.

### 알트코인 분석가 (`alt_structure.py`)
**용도**: 개별 코인 심층 분석 + 차트 생성 + 텔레그램 전송

**핵심 기능**:
1. **Volume Profile 분석**: 50캔들 × 30빈 → POC, HVN(저항/지지), LVN(돌파 구간), Value Area
2. **패턴 감지**: VA 돌파, VA 거부, 스퀴즈→확장, 모멘텀 캔들
3. **BTC 상관관계 시나리오**: 
   - Pearson 상관계수 + 베타(β) 계산 (4h 데이터)
   - BTC ±3%, ±5%, ±10% 시나리오별 알트 예상 가격 + 20x ROE 계산
4. **스퀴즈/캐스케이드 감지 결과 주입**: LLM 프롬프트에 팩트로 포함
5. **LLM 분석**: 롱/숏 진입 추천 (entry, SL, TP, R:R 비율)
6. **차트 생성**: 좌측 캔들차트 + 우측 분석 패널 (20x ROE 표시)

**호출**: 텔레그램 `/<코인>분석` 명령어 또는 `/분석` 명령어

---

### 돌파 전문가 (`breakout_scanner.py`)
**용도**: 워치리스트 전체를 스캔하여 돌파 임박 코인을 찾습니다.

**핵심 로직**:
1. BTC 4h 데이터로 시장 맥락 파악
2. 각 코인에 대해:
   - LVN(저유동성 구간) 위치 확인 → LVN에 없으면 스킵
   - 횡보 강도(0~1) 계산 → 0.3 미만이면 스킵
   - BTC 대비 상대강도(-1~+1) 계산
3. (relative_strength, sideways_score) 내림차순 정렬 → 상위 10개 후보
4. LLM이 최종 돌파 방향/목표가/신뢰도 분석

**핵심 인사이트**: LVN에서 횡보 중인 코인은 돌파 시 빠르게 움직입니다 (유동성 부족으로 가격이 한 번에 점프).

**호출**: 4시간 자동 스캔 + 텔레그램 `/돌파` 명령어

---

### 숏스퀴즈/롱 캐스케이드 종료 감지기 (`squeeze_detector.py`)
**용도**: 숏스퀴즈 또는 롱 캐스케이드 이벤트의 소진 시점을 감지합니다. **LLM 미사용** (규칙 기반, 비용 0, 속도 1초).

**이벤트 판별 기준 (3점 이상)**:
| 숏스퀴즈 시그널 | 점수 | 롱 캐스케이드 시그널 | 점수 |
|----------------|------|---------------------|------|
| 24h +5% 또는 48h +8% | +2 | 24h -5% 또는 48h -8% | +2 |
| 24h +3% 또는 48h +5% | +1 | 24h -3% 또는 48h -5% | +1 |
| OI -3% | +1 | OI -3% | +1 |
| 숏 청산 우위 | +2 | 롱 청산 우위 | +2 |
| 롱 과열 펀딩비 | +1 | 숏 과열 펀딩비 | +1 |

**6개 소진 시그널 (가중치)**:
| 시그널 | 가중치 | 설명 | 소진 판단 |
|--------|--------|------|-----------|
| 가격속도 감속 | 2.0 | ROC 피크 대비 현재 비율 | 감속 = 소진 |
| OI 안정화 | 2.5 | OI 변화율 | ±1.5% 이내 = 안정 |
| 볼륨 감소 | 1.5 | 클라이맥스(2.5배) 후 감소 | 감소 = 소진 |
| RSI 극단 | 1.5 | 과매수80/과매도20 + 반전 | 극단 + 반전 = 소진 |
| 펀딩비 반전 | 2.0 | 극단값(±0.03%) 후 전환 | 전환 = 소진 |
| 청산 감소 | 2.5 | 반대편 청산으로 전환 | **가장 강한 시그널** |

**상태 판정**:
- 0~20%: BUILDING (시작 단계)
- 20~50%: ACTIVE (활발 진행)
- 50~75%: EXHAUSTING (소진 중)
- 75%+: EXHAUSTED (종료 임박 → 반전 기회)

**연동**:
- 알트 분석가 LLM 프롬프트에 `format_compact()` 결과 주입
- 텔레그램 `/스퀴즈` 명령어로 수동 조회
- `/스퀴즈감시 <코인>` 명령어로 1분 모니터링 등록 → 100% 도달 시 자동 알람

---

## 10. 실시간 감지 시스템

### 급락 감지기 (`crash_detector.py`)
15초마다 폴링하여 BTC 급락을 감지합니다.

| 레벨 | 윈도우 | 캔들 수 | 임계값 | ROE 영향 | 쿨다운 | 라벨 |
|------|--------|---------|--------|---------|--------|------|
| 1 | 15분 | 1 | -1.0% | -20% | 900초 | 급변 주의 |
| 2 | 1시간 | 4 | -2.5% | -50% | 900초 | 급락 경고 |
| 3 | 1시간 | 4 | -3.5% | -70% | 300초 | 즉시 대응 |
| 4 | 4시간 | 16 | -5.0% | -100% | 300초 | 크래시 — 청산 임박 |

**볼륨 스파이크**: 최근 거래량 ≥ 12캔들 평균 × 3.0배
**다중 타임프레임**: 모든 4개 레벨의 change_pct + ROE를 동시 계산

**대응 흐름**:
- Level 1: 간략 알림만
- Level 2+: 브레이킹 알림 + 6-에이전트 긴급 분석 (비동기)

---

### 추세 반전 감지기 (`trend_reversal_detector.py`)
15초마다 BTC 추세를 확인하여 보유 포지션과 반대 방향으로 전환되었는지 감지합니다.

**설정**:
- EMA_FAST = 5 (75분), EMA_SLOW = 15 (3시간 45분)
- RSI_PERIOD = 14
- 알림 쿨다운: 30분/포지션

**추세 판별**:
- gap_pct > 0.05% AND slope > 0.02% → "bullish"
- gap_pct < -0.05% AND slope < -0.02% → "bearish"
- 그 외 → "neutral"

**알림 조건**: 추세 변화 + 포지션 방향과 반대 + 쿨다운 만료

---

## 11. 리스크 관리 파이프라인

### RiskPipeline (`risk/pipeline.py`)
4개의 리스크 에이전트가 순차적으로 평가합니다:

```
Step 1: Analyst → 시장 분석 (감성, 변동성 레짐, 핵심 리스크, 기회)
Step 2: Risk → 포지션별 리스크 평가 (리스크 레벨, SL/TP, 사이징)
Step 3: Hedge → 헤지 추천 (none/partial_close/opposite/correlated/full_exit)
Step 4: Leverage → 레버리지 조정 추천 (현재/권장 레버리지, 조정 이유)
    ↓
종합: RiskDecision (리스크 레벨, 긴급도, 추론)
```

### 입력 모델 (`risk/models.py`)
- **PositionContext**: 심볼, 방향, 진입가/현재가, 레버리지, PnL%, SL, TP
- **MarketState**: BTC 가격/변화, RSI, ATR%, 레짐, FGI, 펀딩비, 트렌드
- **PipelineContext**: 시그널, 포지션 목록, 시장 상태, 계좌 잔고, 노출도

### 출력
- **RiskLevel**: low / medium / high / critical
- **HedgeAction**: none / partial_close / opposite_position / correlated_hedge / full_exit
- **Urgency**: low / medium / high

### V9 Hook 연동 (Phase F)
진입 전 `risk_check()` 호출:
- Brain 어드바이스 조회
- RiskPipeline 평가
- 결과: risk_level, urgency, leverage_rec, hedge_action, brain_confidence, decision

---

## 12. 적응형 메모리 시스템

### TradingBrain (`agents/memory/trading_brain.py`)
규칙 기반 자문 시스템으로, 과거 거래에서 학습한 패턴을 기반으로 조언합니다.

**핵심 기능**:
- `consult(signal, market_state)` → 적용 가능한 규칙 + 코인/패턴 승률 + 신뢰도 + 조언
- `record_trade_result(symbol, direction, gap, exit_type, roe)` → 거래 결과 기록 + 승률 업데이트
- 24시간마다 유지보수 (시간 감쇠 적용)

### RuleStore (`agents/memory/rule_store.py`)
거래 규칙 저장소:
- 조건(condition), 행동(action), 신뢰도, 히트/미스 카운트
- 유효 신뢰도 = confidence × weight
- 선택적 ChromaDB 벡터 검색 (의미론적 규칙 매칭)
- JSON 파일 백업 + 스레드 세이프

### ReflectionEngine (`agents/memory/reflection_engine.py`)
거래 종료 후 자동 반성 → 교훈 추출 → 규칙 생성:
1. LLM 기반 반성 (우선) → 새 규칙 생성
2. 규칙 기반 패턴 매칭 (LLM 실패 시):
   - SL 히트 + 방향 맞음 → "동적 SL 필요" 규칙
   - 빠른 TP → "모멘텀 포착 성공" 규칙
   - 긴 타임아웃 → "스퀴즈 만료" 규칙
   - BTC 레벨 피해 → "BTC 필터 강화" 규칙

### DecayEngine (`agents/memory/decay_engine.py`)
시간 감쇠 + 성과 기반 가중치 조정:
- 감쇠 반감기: ~70일 (λ = 0.01)
- 최소 가중치: 0.1 (이하 → 비활성화)
- 성과 조정: hit_rate / 0.5 곱셈 (0.5~1.5 범위)
- 24시간마다 자동 실행

### AnalysisMemory (`agents/memory/store.py`)
계층적 메모리 (FinMem 영감):
- **단기**: 최근 N개 분석 결과 (deque)
- **중기**: 30일 롤링 일별 요약 (dict)
- `get_risk_trend(n)` → DETERIORATING / IMPROVING / STABLE
- `get_multi_day_pattern(n_days)` → 지배적 리스크, 레짐, 신뢰도 추세

### ShadowLogger (`agents/shadow_logger.py`)
배포 전 검증:
- 분석 결과 CSV 로깅 (예측 기록)
- 실제 결과 역추적 (outcome_matched 계산)
- 정확도 리포트: 총 분석 수, 정확도%, 오탐/미탐 비율

---

## 13. 데이터 소스

### 바이낸스 API (CCXT)
| 데이터 | 용도 | 갱신 주기 |
|--------|------|-----------|
| OHLCV (15m/1h/4h) | 시그널 생성, 지표 계산 | 매 스캔 |
| 잔고 | 사이징 계산 | 30초 캐시 |
| 현재가 | 포지션 관리, PnL | 3초 캐시 |
| 포지션 정보 | 동기화 | 5분 |

### CoinGlass API v4 (`coinglass_client.py`)
**Rate Limit**: 30회/분 (슬라이딩 윈도우), 429시 60초 대기

| API | 데이터 | 용도 |
|-----|--------|------|
| OI Change | 미결제약정 변화율 | 자본 유출입 판단 |
| Funding Rate Trend | 3개 8h 펀딩비 | 과열 감지 (LONG/SHORT_OVERHEATED) |
| Global LS Ratio | 전체 롱/숏 비율 | 개미 포지셔닝 (역투자) |
| Top Trader LS Ratio | 상위 트레이더 비율 | 프로 포지셔닝 (추종) |
| Liquidation History | 청산 이력 | 청산 압력 방향 |
| Liquidation Map | 가격별 청산 분포 | 청산 중력 분석 |
| Taker Pressure | 매수/매도 비율 | 즉시 수급 판단 |
| CVD History | 누적 거래량 델타 | 수급 추세 |
| Fear/Greed Index | 시장 감성 | 극단 감성 감지 |

### CoinGlass 종합 분석 (`get_full_analysis`)
Short Conviction Score (-∞ ~ +8):
| 시그널 | 점수 |
|--------|------|
| OI 하락 (자본 유출) | +1 |
| 롱 과열 펀딩 | +2 |
| 숏 과열 펀딩 | -2 |
| 개미 롱 편향 (LS > 1.5) | +1 |
| 프로 숏 (LS < 0.8) | +1 |
| 롱 청산 발생 | +1 |
| 숏 청산 발생 | -1 |
| 강한 매도 압력 | +2 |
| 강한 매수 압력 | -1 |
| 청산 중력 DOWN (≥ 5) | +1 |
| 청산 중력 UP (≥ 5) | -1 |

**판정**: ≥ 4 → STRONG SHORT, ≥ 2 → SHORT FAVORABLE, ≤ -3 → SHORT DANGER, ≤ -1 → CAUTION, 그 외 → NEUTRAL

### 기타 소스
- **CryptoCompare News API**: 뉴스 20개 수집 (무료)
- **Alternative.me**: Fear/Greed Index
- **PAXG/USDT**: 금 가격 프록시

---

## 14. 텔레그램 명령어 체계

### Bot1 명령어
| 명령어 | 기능 |
|--------|------|
| `/help` | 전체 명령어 도움말 |
| `/판결` | 오케스트레이터 전체 분석 (6 에이전트 토론 → 합의) |
| `/분석` | 오케스트레이터 분석 실행 (비동기) |
| `/포지션` | 현재 포지션 상태 |
| `/진입` | 수동 진입 분석 |
| `/시장` | 22코인 시장 상태 스캔 |
| `/상태` | 봇 상태 + BTC 레벨 |
| `/메모리` | Brain 통계 + 반성 통계 |
| `/<코인>분석` | 개별 코인 심층 분석 + 차트 (예: `/SOL분석`) |
| `/돌파` | 돌파 전문가 수동 스캔 |
| `/돌파간격 <초>` | 돌파 스캔 주기 조정 (기본 14400초=4시간) |
| `/스퀴즈 <코인>` | 숏스퀴즈/롱캐스케이드 소진도 조회 |
| `/스퀴즈감시 <코인>` | 1분 모니터링 등록 (100% 소진 시 자동 알람) |
| `/스퀴즈감시해제 <코인>` | 모니터링 해제 |

### Bot2 명령어
Bot1과 동일 구조이며, `self.bot_tag` 접두사를 사용합니다.
(알려진 버그: 도움말에 `/b2분석`이라고 안내하지만 코드는 `/분석`으로 매칭)

---

## 15. 출력 시스템

### 텔레그램 포맷터 (`telegram_formatter.py`)
- **일반 합의**: 리스크 레벨 + 레짐 + BTC 편향 + 에이전트별 요약 + 포지션별 판결
- **긴급 합의**: 급락 상황 + 원인 분석 + 다중 TF ROE 영향 + 긴급 행동
- **메시지 분할**: 4000자 제한 → 줄바꿈 기준 분할

### 차트 생성기 (`chart_generator.py`)
- **좌측 (65%)**: 캔들차트 (다크 테마 #16213e)
  - 60캔들 OHLCV + EMA12(파랑) + EMA26(주황)
  - Value Area(보라), POC(노랑), R(빨강), S(초록)
  - LONG 진입/SL/TP(파랑), SHORT 진입/SL/TP(보라)
- **우측 (35%)**: 텍스트 분석 패널 (#0f3460)
  - 트렌드, 브레이크아웃 확률, 리스크, 신뢰도
  - 레벨 거리 + 20x ROE (R1~R3, POC, S1~S3)
  - LONG/SHORT 추천 (Entry, SL, TP, R:R)
  - 패턴 + 분석 요약
- **한글 폰트**: Windows/Linux 자동 탐색
- **DPI**: 130, PNG 출력

---

## 16. 현재 성과 및 발견된 문제점

### 370만 시그널 라벨링 결과 (실측)
| 지표 | 값 | 의미 |
|------|-----|------|
| **SL 히트율** | 48.6% | 전체 손실의 최대 원인 |
| **SL 히트 중 방향 정확** | 80.1% | **144만 건 개선 기회** (동적 SL) |
| **방향 정확도** | 85.9% | 이미 높음 → 방향 필터 개선 여지 제한 |
| **넓은 GAP 방향 정확도** | 82.6%→90.3% | GAP 0.0~0.3 → GAP 1.0~1.5 |
| **프로덕션 PF** | 1.14 | 수익 팩터 (Level 3까지 확장해도 > 1.0) |

### 핵심 문제: 고정 SL
```
현재: SL = squeeze_low/high ± (squeeze_range × 0.3 × ATR)
문제: 시장 변동성과 무관한 고정 SL → 48.6%가 SL 히트
     그 중 80.1%는 방향이 맞았음에도 조기 청산됨
     = 144만 건의 "방향은 맞지만 SL에 걸린" 거래
```

### 시그널 레벨별 성과
```
프로덕션: conf ≥ 60, ADX ≥ 30, GAP ≤ 0.5%  → PF 1.14
Level 1:  conf ≥ 50, ADX ≥ 25, GAP ≤ 0.7%  → PF > 1.0
Level 2:  conf ≥ 40, ADX ≥ 20, GAP ≤ 0.8%  → PF > 1.0
Level 3:  conf ≥ 30, ADX ≥ 15, GAP ≤ 1.0%  → PF > 1.0
```

### 과거 교훈
1. **XGBoost 오버피팅**: Walk-Forward 없이 단순 split → 백테스트에서만 성과. 이후 3윈도우 검증 의무화
2. **candle_idx 오프바이원**: 미래 데이터 누출 → 시간 기반 분리 강화
3. **FLIP 전략 보류**: 방향 정확도 82~90%로 반전 불필요
4. **outcome_labeler 성능**: 24워커로 370만 시그널 3.7분 처리

---

## 17. 향후 계획 (RL + LLM)

### Phase 4 (최우선): 동적 SL — `rl/agent/sl_agent.py`
144만 건의 "방향 맞지만 SL 히트" 문제 해결:
- RL 에이전트가 시장 상황에 따라 SL 거리를 동적 조절
- 변동성 높을 때 SL 넓히고, 낮을 때 좁히는 학습

### Phase 1: RL 환경 — `rl/env/trading_env.py`
3-Stage 환경:
- Stage 1: 방향 판단 (EXECUTE/SKIP)
- Stage 2: 실행 (진입 타이밍)
- Stage 3: SL 관리 (동적 SL)

### Phase 1.5: 방향 필터
Stage 1에서 EXECUTE/SKIP 학습 (FLIP은 보류)

### Phase 2: LLM 센티먼트
FinBERT + Claude Haiku 티어드 접근

### Walk-Forward 3윈도우 검증 (필수)
```
Window 1: Train ~2025-03 → Val ~2025-06 → Test ~2025-09
Window 2: Train ~2025-06 → Val ~2025-09 → Test ~2025-12
Window 3: Train ~2025-09 → Val ~2025-12 → Test ~2026-03
```

### 목표 라이브러리
- stable-baselines3 (PPO, DQN)
- d3rlpy (오프라인 RL)
- gymnasium (환경 표준)

---

## 18. 파일 구조 맵

```
hello-world/
├── CLAUDE.md                              # 프로젝트 가이드
├── system_overview.md                     # 이 문서
│
├── auto_trader_v9.py                      # [86KB] Bot1 프로덕션 메인봇
├── auto_trader_v9_bot2.py                 # Bot2 Hunt Trader
├── convergence_strategy.py                # EMA 수렴 브레이크아웃 전략 엔진
├── coinglass_client.py                    # CoinGlass API v4 클라이언트
│
├── agents/
│   ├── core/
│   │   ├── base.py                        # AgentBase (LLM 호출 + 비용 추적)
│   │   ├── protocol.py                    # 데이터 모델 (AgentMessage, MarketConsensus 등)
│   │   └── orchestrator.py                # 토론 + 합의 시스템
│   │
│   ├── analyzers/
│   │   ├── btc_structure.py               # Agent 1: BTC 구조 (Factual)
│   │   ├── macro.py                       # Agent 2: 매크로 (Subjective)
│   │   ├── correlation.py                 # Agent 3: 상관관계 (Factual)
│   │   ├── alt_ecosystem.py               # Agent 4: 알트 생태계 (Subjective)
│   │   ├── position_judge.py              # Agent 5: 포지션 심판
│   │   ├── news_sentiment.py              # Agent 6: 뉴스 감성 (Subjective)
│   │   ├── alt_structure.py               # 독립: 알트코인 분석가 (LLM)
│   │   ├── breakout_scanner.py            # 독립: 돌파 전문가 (LLM)
│   │   └── squeeze_detector.py            # 독립: 스퀴즈/캐스케이드 감지 (규칙기반)
│   │
│   ├── memory/
│   │   ├── store.py                       # AnalysisMemory (단기+중기)
│   │   ├── trading_brain.py               # TradingBrain (규칙 자문)
│   │   ├── rule_store.py                  # RuleStore (규칙 저장소)
│   │   ├── reflection_engine.py           # ReflectionEngine (거래 반성)
│   │   └── decay_engine.py                # DecayEngine (시간 감쇠)
│   │
│   ├── output/
│   │   ├── telegram_formatter.py          # 텔레그램 메시지 포맷
│   │   └── chart_generator.py             # 차트 생성 (matplotlib)
│   │
│   ├── v9_hook.py                         # V9 봇 ↔ 에이전트 연결
│   ├── crash_detector.py                  # 급락 감지 (4레벨)
│   ├── trend_reversal_detector.py         # 추세 반전 감지
│   └── shadow_logger.py                   # 쉐도우 모드 로깅
│
├── risk/
│   ├── pipeline.py                        # 4-에이전트 리스크 파이프라인
│   └── models.py                          # Pydantic 리스크 모델
│
├── cloud_scan_v2.py                       # Numba JIT 고속 백테스트 스캐너
├── backtest_entry_quality.py              # 진입 품질 백테스트 (MAE/MFE)
├── outcome_labeler.py                     # 시그널 결과 라벨링 (370만건)
├── download_data.py                       # 바이낸스 OHLCV 다운로드
│
└── tasks/
    └── experiment_plan_rl_llm.md          # RL+LLM 실험계획서 v4
```

---

## 부록: LLM 비용 구조

### 에이전트별 LLM 호출
| 에이전트 | 모델 | 호출 빈도 | 용도 |
|----------|------|-----------|------|
| Agent 1~4, 6 (Context) | Sonnet (Deep) | 정기 4h, 긴급 즉시 | 심층 분석 |
| Agent 5 (Position) | Sonnet (Deep) | 포지션 있을 때 | 판결 |
| Bull/Bear 토론 | Haiku (Quick) × 2 | 합의 시 | 빠른 논거 |
| Moderator | Sonnet (Deep) × 1 | 합의 시 | 최종 합의 |
| 알트 분석가 | Sonnet (Deep) | 수동 명령 | 코인 분석 |
| 돌파 전문가 | Sonnet (Deep) | 4시간 자동 | 후보 분석 |
| 스퀴즈 감지기 | **없음** (규칙) | 1분 (모니터링) | 소진도 |
| ReflectionEngine | Sonnet (Deep) | 포지션 종료 시 | 반성 |

### 비용 최적화 포인트
- 프롬프트 캐싱으로 시스템 프롬프트 토큰 절감
- 스퀴즈 감지기: LLM 미사용으로 비용 0 + 매초 호출 가능
- 정기분석 MVP (3 에이전트)와 긴급분석 Full (6 에이전트) 분리
- Quick(Haiku)/Deep(Sonnet) 이중 모델로 비용 균형

---

## 부록: 핵심 데이터 흐름 요약

```
[바이낸스 15m OHLCV] → [convergence_strategy.detect()] → [시그널]
                                                              ↓
[BTC 7레벨 필터] ← [BTCTrend7Level.update()]          [try_enter()]
                                                              ↓
[RiskPipeline.evaluate()] + [TradingBrain.consult()]   [사이징 계산]
                                                              ↓
                                                       [주문 실행]
                                                              ↓
[manage_positions()] ← 15초 루프                       [포지션 관리]
  ├── SL/TP 히트 확인                                        ↓
  ├── Lock 트리거 (본전 이동)                          [포지션 종료]
  ├── Trail (D모드)                                          ↓
  ├── EE (조기청산)                            [ReflectionEngine.reflect()]
  └── 타임아웃                                               ↓
                                                       [규칙 생성/업데이트]
[CrashDetector.check()] ← 15초 루프
  ├── Level 1: 알림
  └── Level 2+: 6-에이전트 긴급 분석 → 텔레그램

[TrendReversalDetector.check()] ← 15초 루프
  └── 포지션 반대 추세 → 텔레그램 경고
```
