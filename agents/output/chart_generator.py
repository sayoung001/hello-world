"""
chart_generator.py — 알트코인 구조 분석 차트 생성
==================================================
matplotlib로 캔들스틱 + 매물대 + 핵심 레벨 차트 생성.
20x 레버리지 기준 손익률 표시.
"""

from __future__ import annotations
import io
import tempfile
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
from matplotlib.gridspec import GridSpec

KST = timezone(timedelta(hours=9))

# 한글 폰트 설정 (서버 환경별 대응)
try:
    import matplotlib.font_manager as fm
    _font_priority = ["malgun", "nanum", "noto sans cjk", "meiryo", "gothic", "gulim"]
    _kor_fonts = []
    for prio in _font_priority:
        found = [f.name for f in fm.fontManager.ttflist if prio in f.name.lower()]
        _kor_fonts.extend(found)
    if _kor_fonts:
        plt.rcParams["font.family"] = _kor_fonts[0]
    else:
        plt.rcParams["font.family"] = "DejaVu Sans"
except Exception:
    plt.rcParams["font.family"] = "DejaVu Sans"

plt.rcParams["axes.unicode_minus"] = False


def _leverage_pnl(entry: float, target: float, leverage: int = 20) -> float:
    """진입가 대비 목표가의 레버리지 손익률(%) 계산"""
    if entry <= 0:
        return 0.0
    return ((target - entry) / entry) * leverage * 100


def generate_alt_chart(
    coin: str,
    ohlcv_df: pd.DataFrame,
    analysis_result: dict,
    collected_data: dict,
) -> bytes:
    """
    알트코인 구조 분석 차트 생성 → PNG bytes 반환.

    좌측: 캔들스틱 + Volume Profile 영역 + 핵심 레벨 (20x 손익률 표시)
    우측: 분석 요약 패널
    """
    current_price = analysis_result.get("current_price", 0)
    supports = analysis_result.get("key_support", [])
    resistances = analysis_result.get("key_resistance", [])
    poc = analysis_result.get("volume_profile_poc", 0)
    va = analysis_result.get("value_area", {})
    trend = analysis_result.get("trend", "neutral")
    trend_strength = analysis_result.get("trend_strength", "moderate")
    breakout_prob = analysis_result.get("breakout_probability", 0)
    risk_level = analysis_result.get("risk_level", "SAFE")
    confidence = analysis_result.get("confidence", 0)
    reasoning = analysis_result.get("reasoning", "")
    patterns = analysis_result.get("patterns_detected", [])
    long_entry = analysis_result.get("long_entry", {})
    short_entry = analysis_result.get("short_entry", {})

    # 4h 데이터
    d4h = collected_data.get("4h", {})
    d1h = collected_data.get("1h", {})
    rsi_4h = d4h.get("rsi", 0)
    rsi_1h = d1h.get("rsi", 0)
    atr_pct = d4h.get("atr_pct", 0)
    ema_gap = d4h.get("ema_gap_pct", 0)
    vol_ratio = d4h.get("volume_ratio", 0)

    # 차트 레이아웃: 좌(캔들+레벨) 70% | 우(분석 패널) 30%
    fig = plt.figure(figsize=(16, 10), facecolor="#1a1a2e")
    gs = GridSpec(1, 2, width_ratios=[7, 3], figure=fig)
    gs.update(wspace=0.02)

    ax = fig.add_subplot(gs[0])
    ax_text = fig.add_subplot(gs[1])

    # ── 좌측: 캔들스틱 차트 ──
    ax.set_facecolor("#16213e")
    df = ohlcv_df.tail(60).copy().reset_index(drop=True)

    if len(df) < 5:
        ax.text(0.5, 0.5, "데이터 부족", ha="center", va="center",
                color="white", fontsize=16, transform=ax.transAxes)
    else:
        # 캔들스틱
        for i, row in df.iterrows():
            color = "#26a69a" if row["close"] >= row["open"] else "#ef5350"
            # 몸통
            body_low = min(row["open"], row["close"])
            body_high = max(row["open"], row["close"])
            body_height = max(body_high - body_low, (df["high"].max() - df["low"].min()) * 0.001)
            ax.bar(i, body_height, bottom=body_low, width=0.6,
                   color=color, edgecolor=color, linewidth=0.5)
            # 꼬리
            ax.vlines(i, row["low"], row["high"], color=color, linewidth=0.8)

        xlim = (-1, len(df) + 0.5)
        ax.set_xlim(xlim)

        # y축 범위 (여유 포함)
        price_min = df["low"].min()
        price_max = df["high"].max()
        price_range = price_max - price_min
        y_pad = price_range * 0.08
        ax.set_ylim(price_min - y_pad, price_max + y_pad)

        # ── 현재가 라인 ──
        ax.axhline(current_price, color="#ffffff", linewidth=1.5, linestyle="-", alpha=0.9)
        ax.text(len(df) + 0.3, current_price,
                f" ▶ {current_price:.4f}", color="#ffffff", fontsize=9,
                fontweight="bold", va="center")

        # ── Value Area (반투명 영역) ──
        va_low = va.get("low", 0)
        va_high = va.get("high", 0)
        if va_low and va_high:
            ax.axhspan(va_low, va_high, alpha=0.12, color="#7c4dff", label="Value Area")
            ax.axhline(va_low, color="#7c4dff", linewidth=0.8, linestyle="--", alpha=0.6)
            ax.axhline(va_high, color="#7c4dff", linewidth=0.8, linestyle="--", alpha=0.6)

        # ── POC 라인 ──
        if poc:
            ax.axhline(poc, color="#ffab00", linewidth=1.2, linestyle="-.", alpha=0.8)
            pnl = _leverage_pnl(current_price, poc)
            direction = "▲" if poc > current_price else "▼"
            ax.text(-0.5, poc, f" POC {poc:.4f} ({direction}{abs(pnl):.1f}%)",
                    color="#ffab00", fontsize=8, va="center", fontweight="bold")

        # ── 저항 레벨 (빨간) ──
        for lvl in resistances[:4]:
            if price_min - y_pad <= lvl <= price_max + y_pad:
                ax.axhline(lvl, color="#ff5252", linewidth=1.0, linestyle="--", alpha=0.7)
                pnl = _leverage_pnl(current_price, lvl)
                ax.text(len(df) + 0.3, lvl,
                        f" R {lvl:.4f} (+{pnl:.1f}%)",
                        color="#ff5252", fontsize=8, va="center")

        # ── 지지 레벨 (초록) ──
        for lvl in supports[:4]:
            if price_min - y_pad <= lvl <= price_max + y_pad:
                ax.axhline(lvl, color="#69f0ae", linewidth=1.0, linestyle="--", alpha=0.7)
                pnl = _leverage_pnl(current_price, lvl)
                ax.text(len(df) + 0.3, lvl,
                        f" S {lvl:.4f} ({pnl:.1f}%)",
                        color="#69f0ae", fontsize=8, va="center")

        # ── 롱 추천 진입 영역 (파란) ──
        le = long_entry.get("entry_price", 0)
        lsl = long_entry.get("stop_loss", 0)
        ltp = long_entry.get("take_profit", 0)
        if le and price_min - y_pad <= le <= price_max + y_pad:
            ax.axhline(le, color="#29b6f6", linewidth=1.3, linestyle="-", alpha=0.8)
            pnl = _leverage_pnl(current_price, le)
            ax.text(-0.5, le, f" L.Entry {le:.4f}", color="#29b6f6", fontsize=8,
                    va="center", fontweight="bold")
            if lsl and price_min - y_pad <= lsl <= price_max + y_pad:
                ax.axhline(lsl, color="#29b6f6", linewidth=0.8, linestyle=":", alpha=0.5)
                ax.text(-0.5, lsl, f" L.SL {lsl:.4f}", color="#29b6f6", fontsize=7, va="center")
            if ltp and price_min - y_pad <= ltp <= price_max + y_pad:
                ax.axhline(ltp, color="#29b6f6", linewidth=0.8, linestyle=":", alpha=0.5)
                ax.text(-0.5, ltp, f" L.TP {ltp:.4f}", color="#29b6f6", fontsize=7, va="center")

        # ── 숏 추천 진입 영역 (보라) ──
        se = short_entry.get("entry_price", 0)
        ssl = short_entry.get("stop_loss", 0)
        stp = short_entry.get("take_profit", 0)
        if se and price_min - y_pad <= se <= price_max + y_pad:
            ax.axhline(se, color="#ce93d8", linewidth=1.3, linestyle="-", alpha=0.8)
            ax.text(-0.5, se, f" S.Entry {se:.4f}", color="#ce93d8", fontsize=8,
                    va="center", fontweight="bold")
            if ssl and price_min - y_pad <= ssl <= price_max + y_pad:
                ax.axhline(ssl, color="#ce93d8", linewidth=0.8, linestyle=":", alpha=0.5)
                ax.text(-0.5, ssl, f" S.SL {ssl:.4f}", color="#ce93d8", fontsize=7, va="center")
            if stp and price_min - y_pad <= stp <= price_max + y_pad:
                ax.axhline(stp, color="#ce93d8", linewidth=0.8, linestyle=":", alpha=0.5)
                ax.text(-0.5, stp, f" S.TP {stp:.4f}", color="#ce93d8", fontsize=7, va="center")

        # EMA 라인
        if len(df) >= 26:
            ema12 = df["close"].ewm(span=12).mean()
            ema26 = df["close"].ewm(span=26).mean()
            ax.plot(range(len(df)), ema12, color="#42a5f5", linewidth=1.0, alpha=0.7, label="EMA12")
            ax.plot(range(len(df)), ema26, color="#ffa726", linewidth=1.0, alpha=0.7, label="EMA26")

    # 차트 스타일링
    ax.set_title(f"{coin}/USDT  4H Chart", color="white", fontsize=14, fontweight="bold", pad=10)
    ax.tick_params(colors="white", labelsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color("#555")
    ax.spines["left"].set_color("#555")
    ax.yaxis.label.set_color("white")
    ax.grid(True, alpha=0.1, color="white")

    # 범례
    legend_elements = [
        mpatches.Patch(facecolor="#7c4dff", alpha=0.3, label="Value Area"),
        plt.Line2D([0], [0], color="#ffab00", linewidth=1.2, linestyle="-.", label="POC"),
        plt.Line2D([0], [0], color="#ff5252", linewidth=1.0, linestyle="--", label="Resistance"),
        plt.Line2D([0], [0], color="#69f0ae", linewidth=1.0, linestyle="--", label="Support"),
        plt.Line2D([0], [0], color="#29b6f6", linewidth=1.3, label="Long Entry"),
        plt.Line2D([0], [0], color="#ce93d8", linewidth=1.3, label="Short Entry"),
        plt.Line2D([0], [0], color="#42a5f5", linewidth=1.0, label="EMA12"),
        plt.Line2D([0], [0], color="#ffa726", linewidth=1.0, label="EMA26"),
    ]
    ax.legend(handles=legend_elements, loc="upper left", fontsize=7,
              facecolor="#1a1a2e", edgecolor="#555", labelcolor="white")

    # ── 우측: 분석 패널 ──
    ax_text.set_facecolor("#0f3460")
    ax_text.set_xlim(0, 1)
    ax_text.set_ylim(0, 1)
    ax_text.axis("off")

    # 패널 제목
    trend_color = {"bullish": "#26a69a", "bearish": "#ef5350", "neutral": "#aaaaaa"}.get(trend, "#aaa")
    risk_color = {"SAFE": "#69f0ae", "CAUTION": "#ffd54f", "DANGER": "#ff5252"}.get(risk_level, "#aaa")

    y = 0.96
    dy = 0.035

    def _text(x, yy, txt, color="white", size=9, weight="normal", ha="left"):
        ax_text.text(x, yy, txt, color=color, fontsize=size, fontweight=weight,
                     ha=ha, va="top", transform=ax_text.transAxes)

    _text(0.05, y, f"{coin} Structure Analysis", "#ffffff", 12, "bold")
    y -= dy * 1.5

    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    _text(0.05, y, now_str, "#888888", 7)
    y -= dy * 1.5

    # 구분선
    ax_text.plot([0.05, 0.95], [y, y], color="#555", linewidth=0.5,
                 transform=ax_text.transAxes, clip_on=False)
    y -= dy

    # 핵심 지표
    _text(0.05, y, "━ 핵심 지표 ━", "#bbbbbb", 9, "bold")
    y -= dy

    _text(0.05, y, f"추세: {trend.upper()} ({trend_strength})", trend_color, 9, "bold")
    y -= dy

    _text(0.05, y, f"돌파 확률: {breakout_prob:.0%}", "#ffffff", 9)
    y -= dy

    _text(0.05, y, f"리스크: {risk_level}", risk_color, 9, "bold")
    y -= dy

    _text(0.05, y, f"신뢰도: {confidence:.0%}", "#ffffff", 9)
    y -= dy * 1.2

    # 기술적 데이터
    _text(0.05, y, "━ 기술적 데이터 ━", "#bbbbbb", 9, "bold")
    y -= dy

    _text(0.05, y, f"RSI(4h): {rsi_4h:.1f}  |  RSI(1h): {rsi_1h:.1f}", "#ffffff", 8)
    y -= dy

    _text(0.05, y, f"ATR: {atr_pct:.3f}%  |  EMA갭: {ema_gap:.3f}%", "#ffffff", 8)
    y -= dy

    _text(0.05, y, f"거래량 비율: {vol_ratio:.2f}x", "#ffffff", 8)
    y -= dy * 1.2

    # 매물대 거리 (20x 레버리지 손익률)
    _text(0.05, y, "━ 레벨 거리 (20x) ━", "#bbbbbb", 9, "bold")
    y -= dy

    if resistances:
        for i, r in enumerate(resistances[:3]):
            pnl = _leverage_pnl(current_price, r)
            dist_pct = ((r - current_price) / current_price) * 100
            _text(0.05, y, f"R{i+1}: {r:.4f}", "#ff5252", 8)
            _text(0.60, y, f"+{dist_pct:.2f}% → +{pnl:.1f}%", "#ff8a80", 8)
            y -= dy

    if poc:
        pnl = _leverage_pnl(current_price, poc)
        dist_pct = ((poc - current_price) / current_price) * 100
        sign = "+" if dist_pct >= 0 else ""
        _text(0.05, y, f"POC: {poc:.4f}", "#ffab00", 8)
        _text(0.60, y, f"{sign}{dist_pct:.2f}% → {sign}{pnl:.1f}%", "#ffcc80", 8)
        y -= dy

    if supports:
        for i, s in enumerate(supports[:3]):
            pnl = _leverage_pnl(current_price, s)
            dist_pct = ((s - current_price) / current_price) * 100
            _text(0.05, y, f"S{i+1}: {s:.4f}", "#69f0ae", 8)
            _text(0.60, y, f"{dist_pct:.2f}% → {pnl:.1f}%", "#a5d6a7", 8)
            y -= dy

    y -= dy * 0.5

    # ── 롱/숏 추천 진입 ──
    if long_entry.get("entry_price") or short_entry.get("entry_price"):
        _text(0.05, y, "━ 추천 진입 (20x) ━", "#bbbbbb", 9, "bold")
        y -= dy

    if long_entry.get("entry_price"):
        le = long_entry["entry_price"]
        lsl = long_entry.get("stop_loss", 0)
        ltp = long_entry.get("take_profit", 0)
        _text(0.05, y, "LONG", "#29b6f6", 9, "bold")
        _text(0.30, y, f"Entry: {le:.4f}", "#29b6f6", 8)
        y -= dy
        if lsl:
            sl_pnl = _leverage_pnl(le, lsl)
            _text(0.05, y, f"  SL: {lsl:.4f}", "#ff8a80", 8)
            _text(0.55, y, f"Max Loss: {sl_pnl:+.1f}%", "#ff8a80", 8)
            y -= dy
        if ltp:
            tp_pnl = _leverage_pnl(le, ltp)
            _text(0.05, y, f"  TP: {ltp:.4f}", "#a5d6a7", 8)
            _text(0.55, y, f"Target: +{tp_pnl:.1f}%", "#a5d6a7", 8)
            y -= dy
        if lsl and ltp and (le - lsl) > 0:
            rr = (ltp - le) / (le - lsl)
            _text(0.05, y, f"  R:R = 1:{rr:.1f}", "#ffffff", 8)
            y -= dy
        y -= dy * 0.3

    if short_entry.get("entry_price"):
        se = short_entry["entry_price"]
        ssl = short_entry.get("stop_loss", 0)
        stp = short_entry.get("take_profit", 0)
        _text(0.05, y, "SHORT", "#ce93d8", 9, "bold")
        _text(0.30, y, f"Entry: {se:.4f}", "#ce93d8", 8)
        y -= dy
        if ssl:
            sl_pnl = _leverage_pnl(se, ssl, leverage=-20)
            _text(0.05, y, f"  SL: {ssl:.4f}", "#ff8a80", 8)
            sl_loss = ((se - ssl) / se) * 20 * 100
            _text(0.55, y, f"Max Loss: {sl_loss:+.1f}%", "#ff8a80", 8)
            y -= dy
        if stp:
            tp_pnl = ((se - stp) / se) * 20 * 100
            _text(0.05, y, f"  TP: {stp:.4f}", "#a5d6a7", 8)
            _text(0.55, y, f"Target: +{tp_pnl:.1f}%", "#a5d6a7", 8)
            y -= dy
        if ssl and stp and (ssl - se) > 0:
            rr = (se - stp) / (ssl - se)
            _text(0.05, y, f"  R:R = 1:{rr:.1f}", "#ffffff", 8)
            y -= dy
        y -= dy * 0.3

    # 감지된 패턴
    if patterns:
        _text(0.05, y, "━ 감지 패턴 ━", "#bbbbbb", 9, "bold")
        y -= dy
        for p in patterns[:4]:
            _text(0.05, y, f"• {p[:35]}", "#e0e0e0", 8)
            y -= dy
        y -= dy * 0.3

    # 분석 근거
    if reasoning:
        _text(0.05, y, "━ 분석 요약 ━", "#bbbbbb", 9, "bold")
        y -= dy
        # 줄바꿈 처리 (35자마다)
        words = reasoning[:200]
        while words and y > 0.02:
            line = words[:38]
            words = words[38:]
            _text(0.05, y, line, "#e0e0e0", 7.5)
            y -= dy * 0.9

    # 하단 워터마크
    fig.text(0.5, 0.01, "Auto Trade System — Alt Structure Analysis (20x Leverage)",
             ha="center", color="#555555", fontsize=7)

    # PNG로 변환
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
