"""
데이터 경로 중앙 관리.

★ 왜 함수인가 (상수가 아니라)
  `.env`는 `config.env.load_env()`가 **런타임에** 읽는다. 그런데 모듈 최상단에서
  `Path(os.environ.get("STOCK_DATA_DIR", ...))` 로 상수를 만들면 그 값은
  **import 시점**에 고정된다. import는 load_env()보다 먼저 일어나므로
  **.env에 STOCK_DATA_DIR을 적어도 반영되지 않는다.**
  (셸 export로 준 값만 우연히 먹는다 — 재현이 어려운 종류의 버그다.)
  → 호출 시점에 환경변수를 읽는 함수로 제공한다.

STOCK_DATA_DIR은 **데이터 루트 한 곳**을 가리킨다(기본 `data`).
그 아래에 하위 폴더가 자동으로 잡힌다.

    $STOCK_DATA_DIR/
    ├── ohlcv_cache/     일봉 캐시 (+ .meta.json 소스 기록)
    ├── universe/        유니버스 스냅샷 (날짜별)
    ├── earnings/        실적 발표일 캘린더
    ├── profiles/        시간대 거래량 프로파일
    ├── observations/    장중 실측 관측(자가보정 입력)
    └── tracking/        signals.csv (신호·결과 기록)

GCE에서 영구 디스크에 데이터를 두고 싶으면 `.env`에 한 줄만 바꾸면 된다.
    STOCK_DATA_DIR=/mnt/stock-data
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_BASE = "data"


def base_dir() -> Path:
    """데이터 루트. 호출 시점의 환경변수를 읽는다."""
    return Path(os.environ.get("STOCK_DATA_DIR", "").strip() or DEFAULT_BASE)


def _sub(name: str) -> Path:
    return base_dir() / name


def ohlcv_cache_dir() -> Path:
    return _sub("ohlcv_cache")


def universe_dir() -> Path:
    return _sub("universe")


def earnings_dir() -> Path:
    return _sub("earnings")


def profiles_dir() -> Path:
    return _sub("profiles")


def observations_dir() -> Path:
    return _sub("observations")


def tracking_dir() -> Path:
    return _sub("tracking")


def signals_csv() -> Path:
    return tracking_dir() / "signals.csv"


def describe() -> str:
    """진단 출력용 — 지금 어디에 쓰고 있는지 한눈에."""
    b = base_dir().resolve()
    return (f"데이터 루트 {b}"
            + (" (기본값)" if not os.environ.get("STOCK_DATA_DIR") else " (STOCK_DATA_DIR)"))
