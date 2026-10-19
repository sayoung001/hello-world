"""
토큰 사용량·비용 기록 — 추정을 실측으로 바꾼다.

왜 필요한가:
  지금까지 response.usage 를 한 번도 읽지 않았다. 그래서
    (a) 실제 지출을 모른다 — 월 얼마인지 추정밖에 할 수 없었다.
    (b) 프롬프트를 고쳤을 때 토큰이 늘었는지 모른다. 스키마에 필드를 하나
        추가하면 15종목 × 4회만큼 입력이 늘지만 알 방법이 없었다.
    (c) 모델을 바꿨을 때 비용이 어떻게 변했는지 비교할 수 없다.

설계:
  배치 1회 = 1행. signals.csv 와 분리한다 — 사용량은 '종목'이 아니라
  '배치 실행' 단위의 사실이고, 수명과 조회 방식이 다르다.

단가는 코드에 박지 않고 여기 표에 모아둔다. 가격은 바뀌므로, 과거 행은
당시 기록된 cost_usd 를 그대로 두고 표만 갱신한다.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Optional

from stock_auto.config.clock import now_et
from stock_auto.config.paths import tracking_dir

FIELDNAMES = [
    "run_at", "date", "market", "model",
    "n_reco", "n_fail",
    "input_tokens", "output_tokens",
    "cache_read_input_tokens", "cache_creation_input_tokens",
    "cost_usd", "price_basis",
]

# 100만 토큰당 USD (입력, 출력). 기준일을 함께 적어 둔다 — 가격이 바뀌면
# 여기만 고치고, 이미 기록된 행의 cost_usd 는 손대지 않는다.
PRICE_BASIS = "2026-10"
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5":  (4.00, 20.00),
    "claude-opus-5":    (5.00, 25.00),
    "claude-opus-4-8":  (5.00, 25.00),
    "claude-opus-4-7":  (5.00, 25.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5":  (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.00, 5.00),
}
# 캐시 읽기는 입력가의 약 1/10, 캐시 쓰기는 약 1.25배로 본다(대략값).
CACHE_READ_MULT = 0.1
CACHE_WRITE_MULT = 1.25


def default_path() -> str:
    return str(tracking_dir() / "usage.csv")


def estimate_cost(model: str, usage: dict) -> Optional[float]:
    """단가 표에 없는 모델이면 None — 0 으로 적어 '공짜'로 오해되지 않게."""
    p = PRICES.get(str(model).strip())
    if not p:
        return None
    pin, pout = p
    def g(k: str) -> int:
        try:
            return int(usage.get(k) or 0)
        except (TypeError, ValueError):
            return 0
    return (g("input_tokens") / 1e6 * pin
            + g("output_tokens") / 1e6 * pout
            + g("cache_read_input_tokens") / 1e6 * pin * CACHE_READ_MULT
            + g("cache_creation_input_tokens") / 1e6 * pin * CACHE_WRITE_MULT)


def record(market: str, date: str, model: str, usage: dict,
           n_reco: int = 0, n_fail: int = 0,
           base: Optional[str] = None) -> Optional[str]:
    """배치 1회분 기록. 실패해도 배치를 막지 않도록 호출부에서 감싼다."""
    if not usage:
        return None
    p = Path(base or default_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    cost = estimate_cost(model, usage)
    row = {
        "run_at": now_et().strftime("%Y-%m-%d %H:%M %Z"),
        "date": date, "market": market, "model": model,
        "n_reco": n_reco, "n_fail": n_fail,
        "input_tokens": usage.get("input_tokens", ""),
        "output_tokens": usage.get("output_tokens", ""),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", ""),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", ""),
        "cost_usd": "" if cost is None else f"{cost:.6f}",
        "price_basis": PRICE_BASIS if cost is not None else "단가미등록",
    }
    write_header = not p.exists()
    with p.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            w.writeheader()
        w.writerow(row)
    return str(p)


def load(base: Optional[str] = None) -> list[dict[str, Any]]:
    p = Path(base or default_path())
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _f(v) -> float:
    try:
        x = float(v)
        return 0.0 if x != x else x
    except (TypeError, ValueError):
        return 0.0


def summary(rows: Optional[list[dict]] = None,
            base: Optional[str] = None) -> dict:
    rows = load(base) if rows is None else rows
    if not rows:
        return {}
    n = len(rows)
    cost = sum(_f(r.get("cost_usd")) for r in rows)
    tin = sum(_f(r.get("input_tokens")) for r in rows)
    tout = sum(_f(r.get("output_tokens")) for r in rows)
    reco = sum(_f(r.get("n_reco")) for r in rows)
    models = sorted({str(r.get("model", "")) for r in rows if r.get("model")})
    return {"runs": n, "cost_usd": cost, "input": tin, "output": tout,
            "reco": reco, "models": models,
            "per_run": cost / n if n else 0.0,
            "per_reco": cost / reco if reco else 0.0,
            # 월 21영업일 기준 환산 — 실행 1회 = 1영업일 가정
            "monthly": cost / n * 21 if n else 0.0}


def section_md(rows: Optional[list[dict]] = None,
               base: Optional[str] = None) -> list[str]:
    """성과 리포트에 끼울 섹션."""
    s = summary(rows, base)
    L = ["", "━━ 토큰 사용량 · 비용 (실측) ━━"]
    if not s:
        L.append("  아직 기록이 없습니다. 다음 배치부터 쌓입니다 "
                 "(LLM을 켜고 돌려야 기록됩니다).")
        return L
    L.append(f"  배치 {s['runs']:.0f}회 · 추천 {s['reco']:.0f}건 · "
             f"모델 {', '.join(s['models'])}")
    L.append(f"  입력 {s['input']:,.0f} tok · 출력 {s['output']:,.0f} tok")
    L.append(f"  누적 ${s['cost_usd']:.4f} · 배치당 ${s['per_run']:.4f} · "
             f"추천 1건당 ${s['per_reco']:.4f}")
    L.append(f"  → 월 환산 약 ${s['monthly']:.2f} (21영업일 기준, 단가 {PRICE_BASIS})")
    # 토큰 비중이 아니라 **비용 비중**을 보여준다. 출력 단가가 입력의 5배라
    # 토큰으로는 65% 여도 금액으로는 90% 다 — 금액 이야기에 토큰 비율을 쓰면
    # "입력을 줄이면 되겠네"라는 반대 결론으로 이어진다.
    if s["output"] and s["input"] and s["models"]:
        p = PRICES.get(s["models"][0])
        if p:
            cin = s["input"] / 1e6 * p[0]
            cout = s["output"] / 1e6 * p[1]
            if cin + cout > 0:
                L.append(f"  비용 비중: 출력 {cout / (cin + cout) * 100:.0f}% / "
                         f"입력 {cin / (cin + cout) * 100:.0f}% — 입력을 줄여도 "
                         "금액은 거의 안 움직입니다. 줄일 거면 호출 횟수나 출력 길이를.")
    return L


def telegram_line(base: Optional[str] = None) -> str:
    s = summary(base=base)
    if not s:
        return ""
    return (f"💰 LLM 비용 누적 ${s['cost_usd']:.2f} "
            f"(배치당 ${s['per_run']:.3f} · 월 환산 ${s['monthly']:.1f})")
