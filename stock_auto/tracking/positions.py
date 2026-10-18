"""
보유 종목 — `positions.csv` 읽기.

지금은 **읽기만** 한다. 파일이 없으면 빈 목록을 돌려주므로 호출측이 분기할 필요가 없다.

왜 이 파일이 필요한가:
  `watchlist._held()` 가 보유 종목을 감시 우선순위 1순위로 넣으려고 이 모듈을
  import 하는데, 모듈 자체가 없어서 ImportError 가 매번 발생하고 광범위한
  `except Exception` 에 삼켜지고 있었다. 그러면 **진짜 오류(파싱 실패 등)도
  같이 숨는다.** 모듈을 두고 예외를 좁히는 쪽이 맞다.

쓰는 쪽(아직 없는 것):
  - 매도 신호를 보유분만 필터 (지금은 유니버스 전체가 나와 하루 13건씩 온다)
  - 보유 종목은 유니버스에서 빠져도 항상 스캔
  - 실현 손익·환율 집계
  이들은 `금융분석_관점_점검_및_보완필요.md` 4-2 항목이고 아직 구현되지 않았다.
  이 파일은 그때 채울 자리이며, 지금은 '있으면 읽는다'까지만 한다.

파일 형식: $STOCK_DATA_DIR/tracking/positions.csv
  symbol, market, entry_date, entry_price, qty, stop, target, fx_rate, note
  (사람이 손으로 적어도 되고, 나중에 triage 입력과 연결할 수 있다)
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Optional

FIELDNAMES = ["symbol", "market", "entry_date", "entry_price", "qty",
              "stop", "target", "fx_rate", "note"]


def positions_csv() -> Path:
    from stock_auto.config.paths import tracking_dir
    return tracking_dir() / "positions.csv"


def _path(base: Optional[str] = None) -> Path:
    return Path(base) if base else positions_csv()


def open_positions(base: Optional[str] = None) -> list[dict[str, Any]]:
    """
    보유 종목 목록. 파일이 없으면 빈 목록(예외 아님).

    qty 가 0 이하이거나 symbol 이 비면 건너뛴다 — 청산된 행을 지우지 않고
    0 으로 남겨 두는 운영 방식을 허용한다.
    """
    p = _path(base)
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    with p.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            sym = (r.get("symbol") or "").strip()
            if not sym:
                continue
            q = _f(r.get("qty"))
            if q is not None and q <= 0:
                continue
            r["symbol"] = sym
            r.setdefault("market", "US")
            out.append(r)
    return out


def symbols(base: Optional[str] = None,
            market: Optional[str] = None) -> list[str]:
    seen: list[str] = []
    for p in open_positions(base):
        if market and p.get("market") != market:
            continue
        if p["symbol"] not in seen:
            seen.append(p["symbol"])
    return seen


def write_template(base: Optional[str] = None) -> Path:
    """빈 템플릿 생성 — 손으로 채워 쓰기 시작할 수 있게."""
    p = _path(base)
    if p.exists():
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()
    return p


def _f(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return None if x != x else x
    except (TypeError, ValueError):
        return None
