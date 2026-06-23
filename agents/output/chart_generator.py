"""
chart_generator.py — 알트코인 구조 분석 차트 생성
==================================================
matplotlib로 캔들스틱 + 매물대 + 핵심 레벨 차트 생성.
20x 레버리지 기준 손익률 표시.
"""

from __future__ import annotations
import io
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
import matplotlib.font_manager as fm

KST = timezone(timedelta(hours=9))

# ── 한글 폰트 설정 (Windows + Linux 대응) ──
_kor_font_prop = None

def _find_korean_font():
    """시스템에서 한글 폰트를 찾아 FontProperties 반환"""
    global _kor_font_prop
    if _kor_font_prop is not None:
        return _kor_font_prop

    # 1차: Windows 폰트 디렉토리 직접 탐색
    win_font_dirs = [
        Path("C:/Windows/Fonts"),
        Path(os.path.expanduser("~/AppData/Local/Microsoft/Windows/Fonts")),
    ]
    win_font_names = ["malgun.ttf", "malgunbd.ttf", "NanumGothic.ttf",
                      "NanumGothicBold.ttf", "gulim.ttc"]
    for fdir in win_font_dirs:
        if fdir.exists():
            for fname in win_font_names:
                fpath = fdir / fname
                if fpath.exists():
                    _kor_font_prop = fm.FontProperties(fname=str(fpath))
                    return _kor_font_prop

    # 2차: Linux 폰트 매니저에서 탐색
    keywords = ["malgun", "nanum", "noto sans cjk kr", "noto sans kr",
                "meiryo", "gothic", "gulim", "batang"]
    for kw in keywords:
        for f in fm.fontManager.ttflist:
            if kw in f.name.lower() or kw in str(f.fname).lower():
                _kor_font_prop = fm.FontProperties(fname=f.fname)
                return _kor_font_prop

    # 3차: 폴백 (한글 깨질 수 있음)
    _kor_font_prop = fm.FontProperties(family="DejaVu Sans")
    return _kor_font_prop


plt.rcParams["axes.unicode_minus"] = False


def _lev_pnl(entry: float, target: float) -> float:
    """20x 레버리지 손익률(%)"""
    if entry <= 0:
        return 0.0
    return ((target - entry) / entry) * 20 * 100


def generate_alt_chart(
    coin: str,
    ohlcv_df: pd.DataFrame,
    analysis_result: dict,
    collected_data: dict,
) -> bytes:
    """
    알트코인 구조 분석 차트 → PNG bytes.

    좌측: 캔들스틱 + 라인(라벨 최소화) — y축 가격으로 레벨 확인
    우측: 모든 상세 정보 (지표, 레벨, 추천 진입, 패턴, 분석)
    """
    fp = _find_korean_font()

    cur = analysis_result.get("current_price", 0)
    supports = analysis_result.get("key_support", [])
    resistances = analysis_result.get("key_resistance", [])
    poc = analysis_result.get("volume_profile_poc", 0)
    va = analysis_result.get("value_area", {})
    trend = analysis_result.get("trend", "neutral")
    trend_str = analysis_result.get("trend_strength", "moderate")
    bk_prob = analysis_result.get("breakout_probability", 0)
    risk_lv = analysis_result.get("risk_level", "SAFE")
    conf = analysis_result.get("confidence", 0)
    reasoning = analysis_result.get("reasoning", "")
    patterns = analysis_result.get("patterns_detected", [])
    long_e = analysis_result.get("long_entry", {})
    short_e = analysis_result.get("short_entry", {})

    d4h = collected_data.get("4h", {})
    d1h = collected_data.get("1h", {})
    rsi_4h = d4h.get("rsi", 0)
    rsi_1h = d1h.get("rsi", 0)
    atr_pct = d4h.get("atr_pct", 0)
    ema_gap = d4h.get("ema_gap_pct", 0)
    vol_ratio = d4h.get("volume_ratio", 0)

    # ── 레이아웃 ──
    fig = plt.figure(figsize=(18, 11), facecolor="#1a1a2e")
    gs = GridSpec(1, 2, width_ratios=[6.5, 3.5], figure=fig)
    gs.update(wspace=0.03)

    ax = fig.add_subplot(gs[0])
    ax_text = fig.add_subplot(gs[1])

    # ════════════ 좌측: 캔들스틱 차트 (라인만, 라벨 최소화) ════════════
    ax.set_facecolor("#16213e")
    df = ohlcv_df.tail(60).copy().reset_index(drop=True)

    if len(df) < 5:
        ax.text(0.5, 0.5, "Insufficient Data", ha="center", va="center",
                color="white", fontsize=16, transform=ax.transAxes)
    else:
        for i, row in df.iterrows():
            c = "#26a69a" if row["close"] >= row["open"] else "#ef5350"
            bl = min(row["open"], row["close"])
            bh = max(row["open"], row["close"])
            ht = max(bh - bl, (df["high"].max() - df["low"].min()) * 0.001)
            ax.bar(i, ht, bottom=bl, width=0.6, color=c, edgecolor=c, linewidth=0.5)
            ax.vlines(i, row["low"], row["high"], color=c, linewidth=0.8)

        ax.set_xlim(-1, len(df))

        p_min = df["low"].min()
        p_max = df["high"].max()
        p_range = p_max - p_min
        y_pad = p_range * 0.08
        ax.set_ylim(p_min - y_pad, p_max + y_pad)

        def _in_range(v):
            return (p_min - y_pad) <= v <= (p_max + y_pad)

        # 현재가
        ax.axhline(cur, color="#ffffff", linewidth=1.8, linestyle="-", alpha=0.9)

        # Value Area
        va_lo = va.get("low", 0)
        va_hi = va.get("high", 0)
        if va_lo and va_hi:
            ax.axhspan(va_lo, va_hi, alpha=0.10, color="#7c4dff")
            ax.axhline(va_lo, color="#7c4dff", linewidth=0.7, linestyle="--", alpha=0.5)
            ax.axhline(va_hi, color="#7c4dff", linewidth=0.7, linestyle="--", alpha=0.5)

        # POC
        if poc and _in_range(poc):
            ax.axhline(poc, color="#ffab00", linewidth=1.2, linestyle="-.", alpha=0.7)

        # Resistance
        for lvl in resistances[:4]:
            if _in_range(lvl):
                ax.axhline(lvl, color="#ff5252", linewidth=0.9, linestyle="--", alpha=0.6)

        # Support
        for lvl in supports[:4]:
            if _in_range(lvl):
                ax.axhline(lvl, color="#69f0ae", linewidth=0.9, linestyle="--", alpha=0.6)

        # Long Entry / SL / TP
        le_p = long_e.get("entry_price", 0)
        if le_p and _in_range(le_p):
            ax.axhline(le_p, color="#29b6f6", linewidth=1.3, alpha=0.8)
        lsl = long_e.get("stop_loss", 0)
        if lsl and _in_range(lsl):
            ax.axhline(lsl, color="#29b6f6", linewidth=0.7, linestyle=":", alpha=0.4)
        ltp = long_e.get("take_profit", 0)
        if ltp and _in_range(ltp):
            ax.axhline(ltp, color="#29b6f6", linewidth=0.7, linestyle=":", alpha=0.4)

        # Short Entry / SL / TP
        se_p = short_e.get("entry_price", 0)
        if se_p and _in_range(se_p):
            ax.axhline(se_p, color="#ce93d8", linewidth=1.3, alpha=0.8)
        ssl_p = short_e.get("stop_loss", 0)
        if ssl_p and _in_range(ssl_p):
            ax.axhline(ssl_p, color="#ce93d8", linewidth=0.7, linestyle=":", alpha=0.4)
        stp_p = short_e.get("take_profit", 0)
        if stp_p and _in_range(stp_p):
            ax.axhline(stp_p, color="#ce93d8", linewidth=0.7, linestyle=":", alpha=0.4)

        # EMA
        if len(df) >= 26:
            ax.plot(range(len(df)), df["close"].ewm(span=12).mean(),
                    color="#42a5f5", linewidth=1.0, alpha=0.7)
            ax.plot(range(len(df)), df["close"].ewm(span=26).mean(),
                    color="#ffa726", linewidth=1.0, alpha=0.7)

    # 차트 스타일
    ax.set_title(f"{coin}/USDT  4H", color="white", fontsize=14,
                 fontweight="bold", pad=10)
    ax.tick_params(colors="white", labelsize=8)
    for sp in ["top", "right"]:
        ax.spines[sp].set_visible(False)
    for sp in ["bottom", "left"]:
        ax.spines[sp].set_color("#555")
    ax.grid(True, alpha=0.1, color="white")

    legend_items = [
        mpatches.Patch(facecolor="#7c4dff", alpha=0.3, label="Value Area"),
        plt.Line2D([0], [0], color="#ffab00", lw=1.2, ls="-.", label="POC"),
        plt.Line2D([0], [0], color="#ff5252", lw=1.0, ls="--", label="Resistance"),
        plt.Line2D([0], [0], color="#69f0ae", lw=1.0, ls="--", label="Support"),
        plt.Line2D([0], [0], color="#29b6f6", lw=1.3, label="Long Entry"),
        plt.Line2D([0], [0], color="#ce93d8", lw=1.3, label="Short Entry"),
        plt.Line2D([0], [0], color="#42a5f5", lw=1.0, label="EMA12"),
        plt.Line2D([0], [0], color="#ffa726", lw=1.0, label="EMA26"),
    ]
    ax.legend(handles=legend_items, loc="upper left", fontsize=7,
              facecolor="#1a1a2e", edgecolor="#555", labelcolor="white")

    # ════════════ 우측: 분석 패널 ════════════
    ax_text.set_facecolor("#0f3460")
    ax_text.set_xlim(0, 1)
    ax_text.set_ylim(0, 1)
    ax_text.axis("off")

    trend_c = {"bullish": "#26a69a", "bearish": "#ef5350", "neutral": "#aaaaaa"}.get(trend, "#aaa")
    risk_c = {"SAFE": "#69f0ae", "CAUTION": "#ffd54f", "DANGER": "#ff5252"}.get(risk_lv, "#aaa")

    y = 0.97
    dy = 0.028

    def _t(x, yy, txt, color="white", size=9, bold=False):
        w = "bold" if bold else "normal"
        ax_text.text(x, yy, txt, color=color, fontsize=size, fontweight=w,
                     va="top", transform=ax_text.transAxes, fontproperties=fp)

    def _sep(yy):
        ax_text.plot([0.04, 0.96], [yy, yy], color="#444", lw=0.5,
                     transform=ax_text.transAxes, clip_on=False)

    # ── 헤더 ──
    _t(0.05, y, f"{coin} Structure Analysis", "#ffffff", 12, True)
    y -= dy * 1.3
    _t(0.05, y, datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"), "#888888", 7)
    y -= dy * 1.3
    _sep(y); y -= dy * 0.7

    # ── 핵심 지표 ──
    _t(0.05, y, "Trend", "#888", 7)
    _t(0.30, y, f"{trend.upper()} ({trend_str})", trend_c, 9, True)
    y -= dy
    _t(0.05, y, "Breakout", "#888", 7)
    _t(0.30, y, f"{bk_prob:.0%}", "#fff", 9)
    _t(0.55, y, "Risk", "#888", 7)
    _t(0.70, y, risk_lv, risk_c, 9, True)
    y -= dy
    _t(0.05, y, "Confidence", "#888", 7)
    _t(0.30, y, f"{conf:.0%}", "#fff", 9)
    y -= dy
    _t(0.05, y, f"RSI  4h:{rsi_4h:.1f}  1h:{rsi_1h:.1f}", "#ccc", 7.5)
    _t(0.55, y, f"ATR:{atr_pct:.3f}%  Vol:{vol_ratio:.1f}x", "#ccc", 7.5)
    y -= dy * 1.1
    _sep(y); y -= dy * 0.7

    # ── 레벨 거리 (20x) ──
    _t(0.05, y, "Level Distance (20x Leverage)", "#bbbbbb", 8, True)
    y -= dy

    _t(0.05, y, "NOW", "#fff", 8, True)
    _t(0.40, y, f"{cur}", "#fff", 8, True)
    y -= dy * 0.8

    for i, r in enumerate(resistances[:3]):
        pnl = _lev_pnl(cur, r)
        dist = ((r - cur) / cur) * 100
        _t(0.05, y, f"R{i+1}", "#ff5252", 8, True)
        _t(0.15, y, f"{r}", "#ff8a80", 8)
        _t(0.65, y, f"+{dist:.2f}%", "#ff8a80", 7.5)
        _t(0.82, y, f"+{pnl:.1f}%", "#ff5252", 7.5, True)
        y -= dy * 0.8

    if poc:
        pnl = _lev_pnl(cur, poc)
        dist = ((poc - cur) / cur) * 100
        s = "+" if dist >= 0 else ""
        _t(0.05, y, "POC", "#ffab00", 8, True)
        _t(0.15, y, f"{poc}", "#ffcc80", 8)
        _t(0.65, y, f"{s}{dist:.2f}%", "#ffcc80", 7.5)
        _t(0.82, y, f"{s}{pnl:.1f}%", "#ffab00", 7.5, True)
        y -= dy * 0.8

    for i, s in enumerate(supports[:3]):
        pnl = _lev_pnl(cur, s)
        dist = ((s - cur) / cur) * 100
        _t(0.05, y, f"S{i+1}", "#69f0ae", 8, True)
        _t(0.15, y, f"{s}", "#a5d6a7", 8)
        _t(0.65, y, f"{dist:.2f}%", "#a5d6a7", 7.5)
        _t(0.82, y, f"{pnl:.1f}%", "#69f0ae", 7.5, True)
        y -= dy * 0.8

    y -= dy * 0.3
    _sep(y); y -= dy * 0.7

    # ── LONG 추천 ──
    if long_e.get("entry_price"):
        le = long_e["entry_price"]
        lsl = long_e.get("stop_loss", 0)
        ltp = long_e.get("take_profit", 0)
        _t(0.05, y, "LONG", "#29b6f6", 9, True)
        _t(0.25, y, f"Entry {le}", "#29b6f6", 8)
        y -= dy
        if lsl:
            sl_pnl = _lev_pnl(le, lsl)
            _t(0.08, y, f"SL {lsl}", "#ff8a80", 7.5)
            _t(0.55, y, f"Max Loss {sl_pnl:+.1f}%", "#ff8a80", 7.5, True)
            y -= dy * 0.8
        if ltp:
            tp_pnl = _lev_pnl(le, ltp)
            _t(0.08, y, f"TP {ltp}", "#a5d6a7", 7.5)
            _t(0.55, y, f"Target +{tp_pnl:.1f}%", "#a5d6a7", 7.5, True)
            y -= dy * 0.8
        if lsl and ltp and (le - lsl) > 0:
            rr = (ltp - le) / (le - lsl)
            _t(0.08, y, f"R:R = 1:{rr:.1f}", "#ccc", 7.5)
            y -= dy * 0.8
        y -= dy * 0.3

    # ── SHORT 추천 ──
    if short_e.get("entry_price"):
        se = short_e["entry_price"]
        ssl_v = short_e.get("stop_loss", 0)
        stp_v = short_e.get("take_profit", 0)
        _t(0.05, y, "SHORT", "#ce93d8", 9, True)
        _t(0.25, y, f"Entry {se}", "#ce93d8", 8)
        y -= dy
        if ssl_v:
            sl_loss = ((se - ssl_v) / se) * 20 * 100
            _t(0.08, y, f"SL {ssl_v}", "#ff8a80", 7.5)
            _t(0.55, y, f"Max Loss {sl_loss:+.1f}%", "#ff8a80", 7.5, True)
            y -= dy * 0.8
        if stp_v:
            tp_gain = ((se - stp_v) / se) * 20 * 100
            _t(0.08, y, f"TP {stp_v}", "#a5d6a7", 7.5)
            _t(0.55, y, f"Target +{tp_gain:.1f}%", "#a5d6a7", 7.5, True)
            y -= dy * 0.8
        if ssl_v and stp_v and (ssl_v - se) > 0:
            rr = (se - stp_v) / (ssl_v - se)
            _t(0.08, y, f"R:R = 1:{rr:.1f}", "#ccc", 7.5)
            y -= dy * 0.8
        y -= dy * 0.3

    _sep(y); y -= dy * 0.7

    # ── 패턴 ──
    if patterns:
        _t(0.05, y, "Patterns", "#bbbbbb", 8, True)
        y -= dy
        for p in patterns[:3]:
            _t(0.08, y, f"- {p[:28]}", "#e0e0e0", 7)
            y -= dy * 0.8
        y -= dy * 0.3

    # ── 분석 요약 ──
    if reasoning and y > 0.06:
        _t(0.05, y, "Analysis", "#bbbbbb", 8, True)
        y -= dy
        text = reasoning[:200]
        while text and y > 0.02:
            line = text[:28]
            text = text[28:]
            _t(0.05, y, line, "#e0e0e0", 7)
            y -= dy * 0.85

    # 워터마크
    fig.text(0.5, 0.005, "Auto Trade System  |  20x Leverage  |  Alt Structure Analysis",
             ha="center", color="#444444", fontsize=7)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
