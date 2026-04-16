"""
chart_renderer.py — 차트 이미지 렌더링
========================================
15분봉 OHLCV 데이터를 AI 최적화 차트 이미지(PNG)로 렌더링.
mplfinance 기반 (Plotly 대비 서버 환경 호환성 우수).
"""

from __future__ import annotations
import io
import os
import tempfile
from datetime import datetime, timezone, timedelta

import numpy as np

try:
    import pandas as pd
except ImportError:
    pd = None

try:
    import mplfinance as mpf
except ImportError:
    mpf = None

try:
    import matplotlib
    matplotlib.use("Agg")  # 서버 환경 (GUI 없음)
    import matplotlib.pyplot as plt
except ImportError:
    plt = None

KST = timezone(timedelta(hours=9))

# 차트 설정
CANDLE_COUNT = 150      # 150캔들 = 15분봉 × 150 = 약 37.5시간
IMAGE_DPI = 100
IMAGE_SIZE = (12, 8)    # 1200×800px


class ChartRenderer:
    """
    OHLCV 데이터 → AI 최적화 PNG 차트 렌더링.

    포함 요소:
    - 캔들스틱 (OHLC)
    - EMA 12/26 라인
    - 볼린저밴드
    - 볼륨 바
    - 시그널 포인트 마커 (옵션)
    - SL/TP 레벨 수평선 (옵션)
    """

    def __init__(self):
        if mpf is None:
            raise ImportError("mplfinance 필요: pip install mplfinance")
        if pd is None:
            raise ImportError("pandas 필요: pip install pandas")

        # AI 최적화 다크 테마
        self._style = mpf.make_mpf_style(
            base_mpf_style="nightclouds",
            marketcolors=mpf.make_marketcolors(
                up="#00FF88", down="#FF4444",
                wick={"up": "#00FF88", "down": "#FF4444"},
                edge={"up": "#00FF88", "down": "#FF4444"},
                volume={"up": "#00AA66", "down": "#AA3333"},
            ),
            facecolor="#1a1a2e",
            gridcolor="#2a2a4e",
            gridstyle="--",
            y_on_right=True,
        )

    def render(
        self,
        ohlcv: list[list],
        symbol: str = "",
        signal: dict | None = None,
        sl_price: float = 0,
        tp_price: float = 0,
    ) -> bytes:
        """
        OHLCV 데이터 → PNG 바이트.

        Args:
            ohlcv: [[timestamp, open, high, low, close, volume], ...]
            symbol: 코인 심볼
            signal: 시그널 정보 (마커 표시용)
            sl_price: SL 수평선
            tp_price: TP 수평선

        Returns:
            PNG 이미지 바이트
        """
        # DataFrame 변환
        df = self._to_dataframe(ohlcv)
        if df is None or len(df) < 10:
            raise ValueError("최소 10캔들 이상 필요")

        # 최근 CANDLE_COUNT개만 사용
        if len(df) > CANDLE_COUNT:
            df = df.tail(CANDLE_COUNT)

        # EMA 계산
        ema12 = df["Close"].ewm(span=12, adjust=False).mean()
        ema26 = df["Close"].ewm(span=26, adjust=False).mean()

        # 볼린저밴드
        sma20 = df["Close"].rolling(20).mean()
        std20 = df["Close"].rolling(20).std()
        bb_upper = sma20 + 2 * std20
        bb_lower = sma20 - 2 * std20

        # 추가 플롯 (EMA + 볼린저)
        add_plots = [
            mpf.make_addplot(ema12, color="#FFD700", width=1.2, label="EMA12"),
            mpf.make_addplot(ema26, color="#00BFFF", width=1.2, label="EMA26"),
            mpf.make_addplot(bb_upper, color="#8888FF", width=0.7, linestyle="--"),
            mpf.make_addplot(bb_lower, color="#8888FF", width=0.7, linestyle="--"),
        ]

        # SL/TP 수평선
        hlines_dict = {"hlines": [], "colors": [], "linestyle": "--", "linewidths": 1.5}
        if sl_price > 0:
            hlines_dict["hlines"].append(sl_price)
            hlines_dict["colors"].append("#FF0000")
        if tp_price > 0:
            hlines_dict["hlines"].append(tp_price)
            hlines_dict["colors"].append("#00FF00")

        # 렌더링
        buf = io.BytesIO()
        kwargs = {
            "type": "candle",
            "style": self._style,
            "addplot": add_plots,
            "volume": True,
            "figsize": IMAGE_SIZE,
            "title": f"\n{symbol} 15m" if symbol else "",
            "savefig": {"fname": buf, "dpi": IMAGE_DPI, "bbox_inches": "tight"},
            "tight_layout": True,
        }
        if hlines_dict["hlines"]:
            kwargs["hlines"] = hlines_dict

        mpf.plot(df, **kwargs)
        buf.seek(0)
        return buf.read()

    def render_to_file(
        self,
        ohlcv: list[list],
        output_path: str,
        symbol: str = "",
        signal: dict | None = None,
        sl_price: float = 0,
        tp_price: float = 0,
    ) -> str:
        """차트를 파일로 저장. 반환: 파일 경로."""
        img_bytes = self.render(ohlcv, symbol, signal, sl_price, tp_price)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(img_bytes)
        return output_path

    def _to_dataframe(self, ohlcv: list[list]):
        """OHLCV 리스트 → mplfinance용 DataFrame"""
        if not ohlcv:
            return None

        df = pd.DataFrame(ohlcv, columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"])
        df["Timestamp"] = pd.to_datetime(df["Timestamp"], unit="ms")
        df.set_index("Timestamp", inplace=True)
        df = df.astype(float)
        return df
