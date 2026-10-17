"""
추천 종목 차트 — 코인봇 chart_generator 방식을 일봉 주식용으로 옮긴 것.

  matplotlib(Agg) 한 장 PNG → 텔레그램 sendPhoto(캡션 포함)

구성(위→아래):
  1. 캔들 + EMA20/60 + 일목 구름(선행스팬 A/B) + 진입 기준선/손절/목표 수평선
  2. 거래량 (20일 평균선)
  3. RSI(14)

★ 폰트: 리눅스 서버에 한글 폰트가 없는 경우가 대부분이라 차트 안 글자는 영문·숫자로만
  쓴다(한글은 텔레그램 캡션에). 나눔/Noto CJK가 있으면 자동으로 쓴다.
★ 외부 라이브러리 추가 없음 — mplfinance 없이 캔들을 직접 그린다(requirements 유지).
"""

from __future__ import annotations

import io
from typing import Any, Optional

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                    # noqa: E402
from matplotlib import font_manager as fm          # noqa: E402
from matplotlib.gridspec import GridSpec           # noqa: E402

N_BARS = 130          # 표시 봉 수(약 6개월)
DPI = 130

# 색: 미국식(상승 초록 / 하락 빨강) — 국내식과 반대지만 티커가 미국 종목이라 맞춘다
UP, DOWN = "#26a69a", "#ef5350"
BG, PANEL, GRID, TXT = "#111418", "#161a1f", "#2a2f36", "#d6d9de"


def _setup_font() -> None:
    """한글 폰트가 있으면 쓰고, 없으면 조용히 기본 폰트(영문 라벨만 사용)."""
    for name in ("NanumGothic", "Noto Sans CJK KR", "Noto Sans KR", "Malgun Gothic",
                 "AppleGothic"):
        if any(f.name == name for f in fm.fontManager.ttflist):
            plt.rcParams["font.family"] = name
            break
    plt.rcParams["axes.unicode_minus"] = False


def _ichimoku(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """선행스팬 A/B (26일 앞으로 밀지 않고 '오늘 위치'의 구름으로 그린다 — 판단 시점 기준)."""
    h, l = df["High"], df["Low"]
    tenkan = (h.rolling(9).max() + l.rolling(9).min()) / 2
    kijun = (h.rolling(26).max() + l.rolling(26).min()) / 2
    span_a = ((tenkan + kijun) / 2).shift(26)
    span_b = ((h.rolling(52).max() + l.rolling(52).min()) / 2).shift(26)
    return span_a, span_b


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def render_signal_chart(symbol: str, df: pd.DataFrame, plan: Optional[dict] = None,
                        title_note: str = "", n_bars: int = N_BARS) -> bytes:
    """
    symbol : 티커
    df     : 일봉(DatetimeIndex, Open/High/Low/Close/Volume) — 캐시 그대로
    plan   : {'stop': float, 'targets': [..], 'close': float, 'recommend': str,
              'horizon': str, 'conviction': float, 'days_to_earnings': int}
    반환   : PNG bytes
    """
    _setup_font()
    plan = plan or {}
    d = df.dropna(subset=["Open", "High", "Low", "Close"]).copy()
    if d.empty:
        raise ValueError(f"{symbol}: 일봉 없음")
    d["EMA20"] = d["Close"].ewm(span=20, adjust=False).mean()
    d["EMA60"] = d["Close"].ewm(span=60, adjust=False).mean()
    d["VOL20"] = d["Volume"].rolling(20).mean() if "Volume" in d else np.nan
    d["RSI"] = _rsi(d["Close"])
    d["SPAN_A"], d["SPAN_B"] = _ichimoku(d)
    d = d.tail(n_bars)
    x = np.arange(len(d))

    fig = plt.figure(figsize=(11, 8.2), dpi=DPI, facecolor=BG)
    gs = GridSpec(6, 1, figure=fig, hspace=0.06, left=0.06, right=0.90,
                  top=0.93, bottom=0.07)
    ax = fig.add_subplot(gs[0:4, 0])
    axv = fig.add_subplot(gs[4, 0], sharex=ax)
    axr = fig.add_subplot(gs[5, 0], sharex=ax)
    for a in (ax, axv, axr):
        a.set_facecolor(PANEL)
        a.grid(True, color=GRID, lw=0.6, alpha=0.7)
        a.tick_params(colors=TXT, labelsize=8)
        for s in a.spines.values():
            s.set_color(GRID)

    # ── 일목 구름 ──
    sa, sb = d["SPAN_A"].values, d["SPAN_B"].values
    ok = ~np.isnan(sa) & ~np.isnan(sb)
    if ok.any():
        ax.fill_between(x, sa, sb, where=ok & (sa >= sb), color=UP, alpha=0.10, lw=0)
        ax.fill_between(x, sa, sb, where=ok & (sa < sb), color=DOWN, alpha=0.10, lw=0)

    # ── 캔들 ──
    o, h, l, c = (d[k].values for k in ("Open", "High", "Low", "Close"))
    col = np.where(c >= o, UP, DOWN)
    ax.vlines(x, l, h, color=col, lw=0.8)
    body_lo, body_h = np.minimum(o, c), np.abs(c - o)
    body_h = np.where(body_h < (h - l) * 0.02, (h - l) * 0.02, body_h)   # 도지도 보이게
    ax.bar(x, body_h, bottom=body_lo, width=0.65, color=col, lw=0)

    # ── 이동평균 ──
    ax.plot(x, d["EMA20"].values, color="#ffb74d", lw=1.0, label="EMA20")
    ax.plot(x, d["EMA60"].values, color="#64b5f6", lw=1.0, label="EMA60")

    # ── 계획선: 기준가(종가)/손절/목표 ──
    last = float(c[-1])
    ref = _f(plan.get("close")) or last
    stop = _f(plan.get("stop"))
    targets = [t for t in (_f(v) for v in (plan.get("targets") or [])) if t]
    lines = [(ref, "#e0e0e0", "-", f"Ref {ref:,.2f}")]
    if stop:
        lines.append((stop, DOWN, "--", f"Stop {stop:,.2f} ({(stop / ref - 1) * 100:+.1f}%)"))
    for i, t in enumerate(targets[:3], 1):
        lines.append((t, UP, "--", f"T{i} {t:,.2f} ({(t / ref - 1) * 100:+.1f}%)"))
    for y, colr, ls, lab in lines:
        ax.axhline(y, color=colr, ls=ls, lw=0.9, alpha=0.9)
        ax.text(len(d) + 0.8, y, lab, color=colr, fontsize=7.5, va="center",
                ha="left", clip_on=False)
    if stop and targets:   # 손실/이익 구간 음영
        ax.axhspan(stop, ref, color=DOWN, alpha=0.06, lw=0)
        ax.axhspan(ref, max(targets[:3]), color=UP, alpha=0.06, lw=0)

    # y 범위: 캔들 + 계획선 모두 포함
    ys = [l.min(), h.max()] + [y for y, *_ in lines]
    lo_y, hi_y = min(ys), max(ys)
    pad = (hi_y - lo_y) * 0.06
    ax.set_ylim(lo_y - pad, hi_y + pad)
    ax.set_xlim(-1, len(d) + 0.5)
    ax.legend(loc="upper left", fontsize=7.5, frameon=False, labelcolor=TXT)
    plt.setp(ax.get_xticklabels(), visible=False)

    # ── 거래량 ──
    if "Volume" in d:
        axv.bar(x, d["Volume"].values, width=0.65, color=col, alpha=0.75, lw=0)
        axv.plot(x, d["VOL20"].values, color="#ffb74d", lw=0.9)
        axv.yaxis.set_major_formatter(
            matplotlib.ticker.FuncFormatter(lambda v, _: f"{v / 1e6:.0f}M" if v >= 1e6
                                            else f"{v / 1e3:.0f}K"))
    axv.set_ylabel("Vol", color=TXT, fontsize=8)
    plt.setp(axv.get_xticklabels(), visible=False)

    # ── RSI ──
    axr.plot(x, d["RSI"].values, color="#ba68c8", lw=1.0)
    axr.axhline(70, color=DOWN, lw=0.6, ls=":")
    axr.axhline(30, color=UP, lw=0.6, ls=":")
    axr.set_ylim(0, 100)
    axr.set_yticks([30, 50, 70])
    axr.set_ylabel("RSI", color=TXT, fontsize=8)

    # x축 날짜: 월 단위 눈금
    idx = pd.DatetimeIndex(d.index)
    ticks = [i for i in range(1, len(idx)) if idx[i].month != idx[i - 1].month]
    if not ticks or ticks[0] > 8:          # 첫 눈금이 너무 늦으면 시작점도 표시
        ticks.insert(0, 0)
    axr.set_xticks(ticks)
    axr.set_xticklabels([idx[i].strftime("%y-%m") for i in ticks], rotation=0)

    # ── 제목 ──
    rec = str(plan.get("recommend") or "")
    # 한글 폰트가 없는 서버가 대부분 — 차트 안 글자는 영문으로
    hz = {"단타": "short-term", "중단기": "mid-term", "스윙": "swing"}.get(
        str(plan.get("horizon") or ""), str(plan.get("horizon") or ""))
    if any("\uac00" <= ch <= "\ud7a3" for ch in hz) and \
            plt.rcParams["font.family"] in (["sans-serif"], "sans-serif"):
        hz = ""
    conv = _f(plan.get("conviction"))
    dte = plan.get("days_to_earnings")
    head = f"{symbol}   {idx[-1]:%Y-%m-%d}   Close {last:,.2f}"
    sub = "  ".join(s for s in (
        f"[{rec}]" if rec else "", hz, f"conv {conv:.2f}" if conv is not None else "",
        f"earnings D-{int(dte)}" if dte not in (None, "") else "", title_note) if s)
    fig.text(0.06, 0.965, head, color="#ffffff", fontsize=12, weight="bold")
    if sub:
        fig.text(0.06, 0.94, sub, color=TXT, fontsize=8.5)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG)
    plt.close(fig)
    return buf.getvalue()


def caption_for(reco: dict, row: Optional[dict] = None) -> str:
    """텔레그램 사진 캡션(≤1024자) — 한글은 여기서."""
    row = row or {}
    t = str(reco.get("ticker") or row.get("symbol") or "")
    targets = [v for v in (_f(x) for x in (reco.get("targets") or [])) if v]
    parts = [f"📈 {t}  [{reco.get('recommend', '')}/{reco.get('horizon', '')}]  "
             f"확신 {_f(reco.get('conviction')) or 0:.2f}"]
    if reco.get("entry_rule"):
        parts.append(f"진입: {str(reco['entry_rule'])[:160]}")
    stop = _f(reco.get("stop"))
    if stop or targets:
        parts.append("손절 " + (f"{stop:,.2f}" if stop else "-") + " · 목표 "
                     + (", ".join(f"{v:,.2f}" for v in targets[:3]) or "-"))
    reasons = row.get("reasons") or row.get("styles")
    if reasons:
        parts.append(f"근거: {str(reasons)[:120]}")
    return "\n".join(parts)[:1000]


def _f(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None
