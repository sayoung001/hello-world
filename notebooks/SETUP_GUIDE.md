# 🔰 초보자용 설치 가이드 — SL 에이전트

Windows 컴퓨터 + Jupyter 환경 기준. 순서대로 따라하면 됩니다.

---

## 전체 흐름

```
1단계: 코드 다운로드 (GitHub → 내 컴퓨터)
2단계: 파이썬 패키지 설치
3단계: 폴더 구조 확인
4단계: Jupyter에서 노트북 실행
```

---

## 1단계: 코드 다운로드

### 방법 A: GitHub에서 직접 다운로드 (가장 쉬움)

1. 웹 브라우저에서 GitHub 저장소 페이지를 연다
2. 초록색 **`<> Code`** 버튼 클릭
3. **`Download ZIP`** 클릭
4. 다운로드된 ZIP 파일을 `D:\AutoTrade\` 안에 풀기
5. 결과로 `D:\AutoTrade\rl\` 폴더가 생기면 성공

### 방법 B: Git 명령어 (Git이 설치되어 있는 경우)

Windows 키 누르고 **"cmd"** 검색 → **명령 프롬프트** 열기:

```
cd D:\AutoTrade
git clone https://github.com/sayoung001/hello-world.git temp_download
```

그 다음 `temp_download` 안의 `rl` 폴더와 `notebooks` 폴더를 `D:\AutoTrade\`로 복사:

```
xcopy /E /I temp_download\rl D:\AutoTrade\rl
xcopy /E /I temp_download\notebooks D:\AutoTrade\notebooks
```

---

## 2단계: 파이썬 패키지 설치

### Jupyter Notebook 안에서 (가장 쉬움)

Jupyter를 평소처럼 열고, 새 셀에 아래를 붙여넣고 `Shift+Enter`:

```python
!pip install stable-baselines3 gymnasium
```

이미 설치된 `pandas`, `numpy`는 안 해도 됩니다.

### 또는 명령 프롬프트에서

Windows 키 → **"cmd"** 검색 → 명령 프롬프트 열기:

```
pip install stable-baselines3 gymnasium
```

> ⚠️ **"pip을 찾을 수 없습니다" 에러가 나면:**
> 아나콘다를 쓰는 경우 **Anaconda Prompt**를 열어서 실행하세요.
> (Windows 키 → "anaconda" 검색)

---

## 3단계: 폴더 구조 확인

설치가 끝나면 `D:\AutoTrade\` 안이 이렇게 되어야 합니다:

```
D:\AutoTrade\
├── Raw_Data\
│   ├── CRYPTO_BINANCE_15M\          ← 이미 있는 15분봉 데이터
│   │   ├── BTCUSDT.csv
│   │   ├── ETHUSDT.csv
│   │   └── ...
│   └── labeled_signals\             ← 이미 있는 라벨링 데이터
│       └── signals_all_labeled.csv
│
├── rl\                              ← ★ 1단계에서 다운받은 것
│   ├── __init__.py
│   ├── agent\
│   │   ├── __init__.py
│   │   └── sl_agent.py
│   ├── env\
│   │   ├── __init__.py
│   │   └── sl_env.py
│   └── features\
│       ├── __init__.py
│       └── state_builder.py
│
└── notebooks\                       ← ★ 1단계에서 다운받은 것
    ├── train_sl_agent.ipynb
    └── SETUP_GUIDE.md (이 파일)
```

### 확인 방법

Jupyter에서 새 셀에 이걸 실행:

```python
from pathlib import Path

base = Path(r"D:\AutoTrade")

checks = [
    base / "Raw_Data" / "labeled_signals" / "signals_all_labeled.csv",
    base / "Raw_Data" / "CRYPTO_BINANCE_15M",
    base / "rl" / "agent" / "sl_agent.py",
    base / "rl" / "env" / "sl_env.py",
    base / "rl" / "features" / "state_builder.py",
]

for p in checks:
    status = "✅" if p.exists() else "❌ 없음!"
    print(f"{status}  {p}")
```

**전부 ✅가 나와야** 다음 단계로 넘어갈 수 있습니다.

---

## 4단계: Jupyter에서 실행

### 노트북 열기

1. Jupyter Notebook을 평소처럼 연다
2. `D:\AutoTrade\notebooks\` 폴더로 이동
3. **`train_sl_agent.ipynb`** 클릭

### 실행 순서

노트북 안의 셀을 **위에서 아래로 순서대로** `Shift+Enter`로 실행합니다.

| 순서 | 셀 제목 | 하는 일 | 예상 시간 |
|:---:|---------|---------|:---------:|
| 1 | 경로 설정 | 데이터 위치 확인 | 1초 |
| 2 | 데이터 확인 | CSV가 잘 읽히는지 | 수 초 |
| 3 | 관측 벡터 빌드 (1000건) | 데이터 변환 테스트 | 1~2분 |
| 4 | 환경 동작 테스트 | RL 환경 정상 확인 | 1초 |
| 5 | **소량 학습** | 빠른 테스트 (5000스텝) | 3~5분 |
| 6 | **본학습** | 전체 데이터 학습 | 30분~1시간 |

> **중요:** 셀 5까지 에러 없이 통과한 후에 셀 6을 실행하세요!

---

## 자주 발생하는 에러와 해결법

### ❌ `ModuleNotFoundError: No module named 'rl'`

`rl` 폴더가 `D:\AutoTrade\rl\`에 없거나, `sys.path`가 안 잡힌 것.

**해결:** 노트북 맨 위 셀에서 경로 확인:
```python
import sys
sys.path.insert(0, r"D:\AutoTrade")
```

### ❌ `ModuleNotFoundError: No module named 'stable_baselines3'`

패키지가 설치 안 된 것.

**해결:** 셀에서 실행:
```python
!pip install stable-baselines3 gymnasium
```

### ❌ `FileNotFoundError: signals_all_labeled.csv`

CSV 파일 경로가 다른 것.

**해결:** 노트북 셀 1에서 `PROJECT_ROOT`를 실제 경로로 수정:
```python
# 예시: 만약 E드라이브라면
PROJECT_ROOT = Path(r"E:\AutoTrade")
```

### ❌ `KeyError: 'symbol'` 또는 `KeyError: 'sl_price'`

CSV 컬럼명이 코드 기대값과 다른 것.

**해결:** 셀 2에서 실제 컬럼명을 확인한 후 알려주세요. 코드를 수정해 드립니다.

### ❌ `OHLCV 파일을 찾을 수 없습니다`

15분봉 CSV 파일명 형식이 다를 수 있음.

**해결:** 실제 파일명을 확인:
```python
import os
files = os.listdir(r"D:\AutoTrade\Raw_Data\CRYPTO_BINANCE_15M")
print(files[:5])  # 처음 5개 파일명 확인
```

---

## 요약: 최소 실행 3줄

Jupyter에서 이 3개 셀만 순서대로 실행하면 됩니다:

```python
# 셀 1: 설정
import sys
sys.path.insert(0, r"D:\AutoTrade")
```

```python
# 셀 2: 패키지 설치 (최초 1회)
!pip install stable-baselines3 gymnasium
```

```python
# 셀 3: 소량 테스트 학습
from rl.agent.sl_agent import SLAgent, TrainConfig

config = TrainConfig(total_timesteps=5000, n_steps=512, batch_size=128,
                     output_dir=r"D:\AutoTrade\experiments\sl_agent\test_run")
agent = SLAgent(config=config)
results = agent.train(
    signals_csv=r"D:\AutoTrade\Raw_Data\labeled_signals\signals_all_labeled.csv",
    ohlcv_dir=r"D:\AutoTrade\Raw_Data\CRYPTO_BINANCE_15M",
    config=config,
    max_signals=2000,
)
```

여기까지 에러 없이 돌아가면 환경 설정 완료!
