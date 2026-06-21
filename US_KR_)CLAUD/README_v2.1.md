# 📊 트레이딩 시스템 v2.1 — 실효 점수 체계 업그레이드

## 🔥 핵심 변경: "기술적 가능성" → "자금이 밀어줄 확률"

### 문제 진단
| 현상 | 원인 | 해결 |
|------|------|------|
| 7점은 맞고, 6점은 틀림 | 기술지표끼리 자기공명 (거래량 없음) | Money/Price 신호 분리 |
| 거래량 없는데 점수 높음 | 거래대금이 필터가 아닌 보조 | Liquidity_Score 곱셈 |
| 단발성 거래량에 속음 | RVOL만 봄 (1단계) | VAI 2단계 가속도 |
| 윗꼬리 종목 탈락 안 됨 | 패널티 시스템 없음 | Penalty 차감 도입 |
| Sector_Status가 `-` | 섹터 분석 미연동 | sector_analysis.py 신규 |

---

## 📐 실효 점수 (Effective Score) 구조

```
Effective_Score = Technical_Score × Liquidity_Score - Penalty

where:
  Technical_Score = Money_Score + Price_Score
  (단, Money_Score == 0이면 Price_Score 최대 2.0으로 Cap)
```

### 구성 요소

#### 1. Money-Driven Signals (가중치 높음)
| 전략 | 가중치 | 조건 핵심 |
|------|--------|-----------|
| Vol Breakout | 2.0 | RVOL>2 + 20일 고가 갱신 + VAI 단발성 제외 |
| Vol Pump Sustained | 2.0 | VAI 2단 지속 + 양봉 + 유동성 0.7+ |
| Money Flow Surge | 1.5 | MFI>60 + OBV 상승 + RVOL>1.5 |

#### 2. Price-Driven Signals (기본 가중치)
| 전략 | 가중치 | 조건 핵심 |
|------|--------|-----------|
| Mean Reversion | 1.5 | RSI<30 + BB%B<0.1 + 양봉 |
| Gap Momentum | 1.0 | 갭 1.5%+ + 양봉 |
| Pullback Buy | 1.5 | EMA20 터치 반등 + RSI 40-60 |
| MACD Reversal | 1.0 | 히스토그램 양전환 + OBV 상승 |
| Squeeze Breakout | 1.5 | BB+KC 수렴 3일 후 돌파 |
| Ichimoku Trend | 1.0 | 삼역호전 |
| Trend Continuation | 1.5 | 완전 정배열 + ADX>25 |

#### 3. Money Cap Rule ⭐
```
Money_Score == 0 → Price_Score 최대 2.0까지만 인정
```
→ "거래량 없이 기술지표만으로 6점" 현상 원천 차단

#### 4. Liquidity Score (연속함수)
```
sigmoid 기반 — Cliff Effect 완화
미국장: $500만 이하 → ~0.4 / $1억 → ~1.0 / $5억+ → ~1.3
한국장: 30억 이하 → ~0.4 / 300억 → ~1.0 / 1000억+ → ~1.3
```

#### 5. Penalty (페널티 차감)
| 조건 | 감점 | 이유 |
|------|------|------|
| 윗꼬리 비율 > 60% | -0.5 | 매도 압력 강함 |
| 고가→종가 하락 > 1.5% | -0.5 | 장중 밀림 |
| VAI 단발성 (1단만 높음) | -1.0 | 가장 큰 페널티 |
| Liquidity < 0.5 | -0.5 | 유동성 부족 |

---

## 📈 VAI (Volume Acceleration Index) — 2단계

```
1단: VAI_Stage1 = 오늘 거래량 / 어제까지 3일 평균 (★shift 적용)
2단: VAI_Stage2 = 어제까지 3일 평균 / 어제까지 20일 평균

해석:
- 1단만 높음 → 단발 펌핑 (vai_spike_only = True → Penalty -1.0)
- 2단 연속 상승 → 자금 유입이 쌓이는 중 (vai_sustained = True)
```

> Data Leakage 방지: 비교 기준은 반드시 `shift(1)` 적용

---

## 🌍 섹터 분석 (sector_analysis.py)

### 사용법
```bash
# 단독 실행 (차트 + CSV 생성)
python sector_analysis.py --market US

# run_analysis와 연동
python run_analysis.py --market US_ALL --run-sector
```

### 섹터 상태 5단계
| 상태 | Score | 조건 | 행동 |
|------|-------|------|------|
| 강력매수 | 5 | Regime≥3 + 매수신호 + VAI 지속 | 적극 매수 |
| 상승추세 | 4 | Regime≥3 or 정배열+ADX>20 | 매수 우선 |
| 회복중 | 3 | 보합 + improving 전환 | 관찰 후 진입 |
| 보합 | 2 | 혼조 | 관망 |
| 하락초기 | 1 | 역배열 시작 | 회피 |
| 하락위험 | 0 | 역배열 + 거래량 증가 | 절대 회피 |

### 출력 파일
```
./Results/Sector_Analysis/{날짜}/
  ├── Sector_Status_Summary.csv    ← run_analysis.py가 읽는 파일
  ├── Good_Sectors/
  │   ├── XLK_기술.png
  │   └── ...
  └── Bad_Sectors/
      ├── XLE_에너지.png
      └── ...
```

### CSV → Sector_Status 연동
```python
# run_analysis.py 내부에서 자동 처리
from sector_analysis import load_sector_status, get_sector_status_for_stock

status_map = load_sector_status()  # CSV 읽기
sector_status = get_sector_status_for_stock('XLK', status_map)  # → '상승추세'
```

---

## 📂 파일 구조 (v2.1)

```
├── indicators_v2.py      # 보조지표 (VAI, Liquidity, Penalty 추가)
├── strategies.py          # 전략 (Money/Price 분리, Effective Score)
├── regime_v2.py           # 5단계 Regime
├── sector_map.py          # GICS 섹터 매핑
├── sector_analysis.py     # ★ 신규: 섹터 분석 + 차트 + CSV
├── run_analysis.py        # 통합 실행 (섹터 연동)
└── README_v2.1.md         # 이 문서
```

---

## 🚀 실행 워크플로우

```bash
# Step 1: 섹터 분석 (하루 1번)
python sector_analysis.py --market US

# Step 2: 종목 분석 (섹터 상태 자동 반영)
python run_analysis.py --market US_ALL --run-sector

# 또는 한번에:
python run_analysis.py --market US_ALL --run-sector
```

---

## 📊 최종 CSV 컬럼 (Buy_Signals)

| 컬럼 | 설명 | v2.1 변경 |
|------|------|-----------|
| Technical_Score | 기술 점수 (Money+Price) | ★ 신규 |
| Money_Score | 자금 신호 점수 | ★ 신규 |
| Price_Score | 기술지표 점수 | ★ 신규 |
| Liquidity_Score | 거래대금 점수 (0.3~1.4) | ★ 신규 |
| Penalty | 페널티 점수 | ★ 신규 |
| Effective_Score | 실효 점수 (최종) | ★ 신규 (= Composite_Score) |
| VAI_Stage1 | 거래량 가속도 1단 | ★ 신규 |
| VAI_Stage2 | 거래량 가속도 2단 | ★ 신규 |
| VAI_Sustained | 지속적 자금 유입 | ★ 신규 |
| Money_Strategies | 활성 자금 전략 | ★ 신규 |
| Upper_Wick_Ratio | 윗꼬리 비율 | ★ 신규 |
| High_Close_Drop(%) | 고가→종가 하락률 | ★ 신규 |
| Sector_Status | 섹터 상태 | ★ 이제 값 들어감 |
| Warning | 경고 내용 상세화 | ★ 개선 |

---

## ✅ 매수 판단 최종 조건

```python
# Case 1: 실효 점수 4점 이상
Effective_Score >= 4.0

# Case 2: 자금 확인된 중간 점수
Effective_Score >= 2.5 AND Money_Score >= 1.5 AND Liquidity >= 0.7

# 공통 필터
AND Warning_Count < 2
AND Penalty < 2.0
AND Regime >= 1 (Regime 0 = 매수 차단)
```

---

## 💡 기존 시스템과의 차이 체감

| 상황 | v2.0 (기존) | v2.1 (신규) |
|------|-------------|-------------|
| 기술6점 + 거래량없음 | Composite 6.0 → 매수 | Effective ~1.2 → 탈락 |
| 기술4점 + 거래량폭발 | Composite 4.0 → 보류 | Effective ~5.2 → 매수 |
| 7점 + 윗꼬리 + 단발거래량 | Composite 7.0 → 최우선 | Effective ~3.5 → 주의 |
| 3점 + 섹터강력매수 + VAI지속 | Composite 3.0 → 탈락 | Effective ~4.0 → 매수후보 |
