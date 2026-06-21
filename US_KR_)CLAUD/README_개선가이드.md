# 📊 나스닥/코스피 시장 분석 시스템 — 개선 가이드

## 🔍 기존 코드 진단 및 개선 요약

### 발견된 문제점

| # | 문제 | 원인 | 해결 |
|---|------|------|------|
| 1 | **단순한 전략** | 6개 기본 전략만 존재, 단타/중단기 구분 없음 | 8개 신규 전략 추가 (단타 3 + 중단기 3 + 스윙 2) |
| 2 | **섹터 불일치** | 키워드 매칭 방식으로 HTS와 다른 결과 | GICS 공식 기준 + 정확한 Industry 매핑 테이블 |
| 3 | **손절/타겟 불명확** | Optuna에만 의존, ATR 기반 기본값 없음 | 전략별 ATR 배수 + Regime별 동적 조절 |
| 4 | **미장/국장 분리** | 별도 노트북으로 관리, 코드 중복 | 통합 `run_analysis.py`로 `--market` 플래그 분기 |
| 5 | **코드 구조** | indicators.py에 중복 import, 함수 뒤섞임 | 모듈별 분리 (indicators / regime / strategies / sector_map) |
| 6 | **매도 전략 부재** | 매수만 있고 매도 기준 없음 | 5개 매도 신호 추가 (Exit Strategy) |
| 7 | **Regime 단순** | 3단계(0/1/2)만 구분 | 5단계(0~4)로 세분화 + 전환 감지 |

---

## 📁 파일 구조

```
프로젝트/
├── indicators_v2.py      ← 보조지표 계산 (Stochastic, Ichimoku, Keltner 추가)
├── regime_v2.py          ← 시장 국면 분석 (5단계 + 전환 감지)
├── strategies.py         ← 8개 매매 전략 + 매도 전략
├── sector_map.py         ← GICS 기반 섹터 분류 (HTS와 일치)
├── run_analysis.py       ← 통합 분석 스크립트 (미장/국장)
├── telegram_msg.py       ← 텔레그램 알림 (기존 유지)
├── nasdaq_etf_list.csv   ← 레버리지 ETF 매핑 (기존 유지)
└── earnings_calendar.csv ← 실적 캘린더 (기존 유지)
```

---

## 🎯 8개 매매 전략 상세

### 단타 (1~3일 보유)

#### 전략 1: Mean Reversion (평균 회귀)
- **컨셉**: 과매도 바닥에서 반등을 잡는다
- **조건**: RSI<30 + 볼린저 하단(%B<0.1) + MFI<35 + 당일 양봉
- **타겟**: 볼린저 중심선(MA20) → 보통 +3~5%
- **손절**: 전일 저가 -1×ATR → 보통 -2~3%
- **기대 승률**: 60~65% (작게 먹고 확실하게)
- **핵심**: 4개 조건을 모두 충족해야 하므로 신호는 적지만 확률이 높음

#### 전략 2: Volume Breakout (거래량 돌파)
- **컨셉**: 평소 2배 이상 거래량 + 20일 신고가 돌파
- **조건**: RVOL>2.0 + 양봉 + 20일 고가 갱신 + ADX>20
- **타겟**: 종가 +2×ATR
- **손절**: 당일 저가
- **기대 승률**: 55~60% (돌파 실패 리스크 있지만 수익폭 큼)

#### 전략 3: Gap Momentum (갭 모멘텀)
- **컨셉**: 1.5% 이상 갭업 후 장중 밀리지 않으면 추세 지속
- **조건**: 갭업 >1.5% + 종가≥시가 + 거래량 평균 이상
- **타겟**: +1.5×ATR
- **손절**: 시가 -0.5% (갭 메워지면 즉시 손절)
- **기대 승률**: 50~55% (R:R로 기대값 양수)

### 중단기 (3~10일 보유)

#### 전략 4: Pullback Buy (눌림목 매수) ⭐ 가장 안정적
- **컨셉**: 확실한 상승추세에서 EMA20까지 눌렸다가 반등
- **조건**: 정배열(EMA20>EMA60) + EMA20 터치 + 종가 회복 + RSI 40~60
- **타겟**: 직전 Swing High (보통 +5~8%)
- **손절**: EMA60
- **기대 승률**: 55~60% (R:R 비율이 2:1 이상)
- **핵심**: 추세가 확실한 종목에서만 작동. 가장 안전한 전략.

#### 전략 5: MACD Reversal (MACD 반전)
- **컨셉**: MACD 히스토그램이 음→양 전환 + OBV 자금 유입
- **조건**: 히스토그램 전환 + 골든크로스 + EMA60 위 + OBV>MA20
- **타겟**: +2×ATR
- **손절**: -1.5×ATR
- **기대 승률**: 55%

#### 전략 6: Squeeze Breakout (스퀴즈 돌파) ⭐ 수익폭 최대
- **컨셉**: BB가 KC 안에 수렴(에너지 축적) 후 폭발 돌파
- **조건**: 최근 5일 중 3일 Squeeze → 오늘 해제 + 양봉 + BB 상단 돌파
- **타겟**: +2.5×ATR (수익폭 큼)
- **손절**: MA20
- **기대 승률**: 55% (수익폭이 커서 기대값 높음)

### 스윙 (10~30일 보유)

#### 전략 7: Ichimoku Trend (일목균형표)
- **컨셉**: 전환선>기준선 + 구름 위 = 강한 상승 추세
- **조건**: 종가>전환선>기준선 + 구름 위
- **타겟**: +3×ATR
- **손절**: 기준선 아래
- **기대 승률**: 50~55% (장기 추세 추종)

#### 전략 8: Trend Continuation (추세 지속)
- **컨셉**: 5/10/20/60 완전 정배열 + ADX 강세
- **조건**: 완전 정배열 + ADX>25 + RSI 50~70 + OBV 상승
- **타겟**: +3×ATR
- **손절**: MA20
- **기대 승률**: 50~55%

---

## 🛡️ 손절/타겟 체계 (기존 대비 개선)

### 기존 문제
Optuna가 매번 다른 값을 뱉어서 일관성이 없었음.

### 개선된 체계

**1단계: 전략 기반 (가장 우선)**
- 각 전략마다 고유한 ATR 배수가 정해져 있음
- 여러 전략이 동시 활성화되면 → 가장 보수적 타겟 + 가장 타이트한 손절

**2단계: Regime 기반 (Fallback)**
| Regime | 타겟(ATR×) | 손절(ATR×) | 비고 |
|--------|-----------|-----------|------|
| 4 (강한 상승) | 2.5 | 1.5 | 공격적 |
| 3 (상승 초기) | 2.0 | 1.2 | 기본 |
| 2 (보합) | 1.5 | 1.0 | 보수적 |
| 1 (하락 초기) | 1.0 | 0.8 | 방어적 |
| 0 (강한 하락) | 매수 차단 | - | 관망 |

**3단계: Optuna 오버라이드 (선택)**
- `--optuna` 플래그로 활성화
- 1/2단계 값을 데이터 기반으로 미세 조정

---

## ⚡ 매도 전략 (Exit Signals) — 신규 추가

기존에는 매수 신호만 있고 매도 기준이 없었습니다.

| # | 매도 신호 | 조건 |
|---|-----------|------|
| 1 | RSI 극과매수 | RSI > 80 |
| 2 | 20일선 이탈 | 종가가 EMA20 하향 돌파 |
| 3 | MACD 데드크로스 | MACD선이 시그널선 하향 교차 |
| 4 | 하락 장악형 | Bearish Engulfing 캔들 |
| 5 | Distribution | 거래량 증가하며 하락 + OBV 하락 |

**판단**: 5개 중 **2개 이상** 충족 시 → 매도 신호 발생

---

## 🗂️ 섹터 분류 개선 (GICS 기준)

### 기존 문제
키워드 매칭 방식이라 "Internet Retail"이 Technology로 분류되는 등 오류 발생.

### 개선
1. **GICS 공식 매핑**: S&P/MSCI 기준 11개 섹터 정확히 매핑
2. **Industry 매핑 테이블**: yfinance/FDR의 Industry 문자열 → GICS 섹터 변환
3. **3단계 Fallback**: 정확매칭 → Industry 테이블 → 키워드 매칭

---

## 🖥️ 사용법

### 기본 실행

```bash
# 미국 전체 분석
python run_analysis.py --market US_ALL

# 특정 종목만 (빠른 테스트)
python run_analysis.py --market US_ALL --tickers TSLA NVDA SOXL

# 한국 전체 분석
python run_analysis.py --market KR_ALL

# Optuna 최적화 포함
python run_analysis.py --market US_ALL --optuna
```

### 노트북에서 실행

```python
from run_analysis import run_analysis

# 미국 특정 종목
run_analysis(market='US_ALL', target_tickers=['TSLA', 'NVDA'])

# 한국 전체
run_analysis(market='KR_ALL')
```

### 개별 모듈 사용

```python
from indicators_v2 import calculate_indicators
from regime_v2 import add_market_regime
from strategies import apply_all_strategies, get_active_strategies

# 단일 종목 분석
import pandas as pd
df = pd.read_csv('NVDA.csv', index_col='Date', parse_dates=True)
df = calculate_indicators(df)
df = add_market_regime(df)
df = apply_all_strategies(df, regime=df['Regime'].iloc[-1])

# 활성 전략 확인
active = get_active_strategies(df.iloc[-1])
print(active)
# [{'name': 'PullbackBuy', 'style': '중단기', 'hold': '3~7일', ...}]
```

---

## 📌 투자 전략 가이드 (단타/중단기)

### 단타 (1~3일) 체크리스트
1. ✅ Regime ≥ 2 (최소 보합 이상)
2. ✅ 거래량 확인 (RVOL > 1.5)
3. ✅ Mean Reversion 또는 Vol Breakout 신호
4. ✅ 손절 라인 진입 전에 확인
5. ✅ R:R 최소 1.5:1 이상
6. ❌ 실적 발표 D-3 이내 종목 회피

### 중단기 (3~10일) 체크리스트
1. ✅ Regime ≥ 3 (상승 추세 확인)
2. ✅ Pullback Buy 또는 Squeeze Breakout 신호
3. ✅ 섹터 상태 확인 (섹터 ETF도 상승 중인지)
4. ✅ 매물대 분석 (위쪽 저항 약해야 함, Hurdle_Score < 0.3)
5. ✅ R:R 최소 2:1 이상
6. ✅ 분할 매수 (1차 50% → 눌림목 확인 후 2차 50%)

### 핵심 원칙
- **Regime 0(약세장)에서는 절대 매수하지 않음**
- **경고 신호 2개 이상이면 매수 차단**
- **손절은 기계적으로 실행 (감정 배제)**
- **하나의 종목에 총 자산의 10% 이상 투입하지 않음**
