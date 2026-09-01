"""
sector_analysis.py — 섹터 분석 모듈
=====================================
[기능]
1. 12개 주요 섹터 ETF의 추세/신호 분석
2. Good/Bad 폴더로 차트 분류 저장
3. Sector_Status_Summary.csv 생성
4. run_analysis.py에서 Sector_Status 컬럼에 연동

[사용법]
  단독 실행:    python sector_analysis.py
  모듈 import:  from sector_analysis import load_sector_status, get_sector_status_for_stock

[출력 파일]
  ./Results/Sector_Analysis/{날짜}/Sector_Status_Summary.csv
  ./Results/Sector_Analysis/{날짜}/Good_Sectors/*.png
  ./Results/Sector_Analysis/{날짜}/Bad_Sectors/*.png
"""

import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta
import warnings

# matplotlib / tqdm 은 차트·진행바 전용이므로 지연 로드한다.
# 일일 배치가 diagnose_sector_status 만 쓰려고 이 모듈을 import 할 때
# 차트 라이브러리까지 요구하면 배치가 불필요하게 무거워지고 설치 의존이 생긴다.
def _plt():
    import matplotlib
    matplotlib.use('Agg')  # 서버환경 호환
    import matplotlib.pyplot as plt
    return plt

warnings.filterwarnings('ignore')

# 모듈 임포트
from stock_auto.indicators.indicators_v2 import (
    calculate_indicators, check_immediate_hurdle,
    analyze_volume_profile, check_and_fix_columns
)
from stock_auto.strategy.regime_v2 import add_market_regime, detect_regime_transition

# ============================================================
# 설정
# ============================================================

TODAY = datetime.now().strftime('%Y-%m-%d')
BASE_DIR = f"./Results/Sector_Analysis/{TODAY}"
GOOD_DIR = f"{BASE_DIR}/Good_Sectors"
BAD_DIR = f"{BASE_DIR}/Bad_Sectors"
SUMMARY_FILE = f"{BASE_DIR}/Sector_Status_Summary.csv"

# ── 미국 섹터 ETF 리스트 (GICS 11개 + 반도체) ──
US_SECTOR_TICKERS = {
    'XLK':  {'name': '기술 (Technology)',           'gics': 'Information Technology'},
    'SOXX': {'name': '반도체 (Semiconductor)',       'gics': 'Semiconductors'},
    'XLC':  {'name': '커뮤니케이션 (Communication)', 'gics': 'Communication Services'},
    'XLY':  {'name': '임의소비재 (Discretionary)',   'gics': 'Consumer Discretionary'},
    'XLF':  {'name': '금융 (Financial)',             'gics': 'Financials'},
    'XLV':  {'name': '헬스케어 (Health Care)',       'gics': 'Health Care'},
    'XLI':  {'name': '산업재 (Industrial)',          'gics': 'Industrials'},
    'XLE':  {'name': '에너지 (Energy)',              'gics': 'Energy'},
    'XLB':  {'name': '소재 (Materials)',             'gics': 'Materials'},
    'XLP':  {'name': '필수소비재 (Staples)',         'gics': 'Consumer Staples'},
    'XLU':  {'name': '유틸리티 (Utilities)',         'gics': 'Utilities'},
    'XLRE': {'name': '부동산 (Real Estate)',         'gics': 'Real Estate'},
}

# ── 한국 섹터 ETF 리스트 ──
KR_SECTOR_TICKERS = {
    '091160': {'name': 'KODEX 반도체',       'gics': 'Semiconductors'},
    '091170': {'name': 'KODEX 은행',         'gics': 'Financials'},
    '091180': {'name': 'KODEX 자동차',       'gics': 'Consumer Discretionary'},
    '117700': {'name': 'KODEX 건설',         'gics': 'Industrials'},
    '117460': {'name': 'KODEX 에너지화학',   'gics': 'Energy'},
    '266370': {'name': 'KODEX 바이오',       'gics': 'Health Care'},
    '305720': {'name': 'KODEX 2차전지',      'gics': 'Materials'},
    '244580': {'name': 'KODEX 바이오(대형)', 'gics': 'Health Care'},
}


# ============================================================
# 섹터 상태 진단 로직
# ============================================================

def diagnose_sector_status(df, last_row, regime_info):
    """
    섹터 상태를 5단계로 진단
    
    [상태 정의]
    ┌──────────────┬──────────────────────────────────────┐
    │ 강력 매수     │ Regime≥3 + 매수신호 + VAI 지속       │
    │ 상승 추세     │ Regime≥3 or (정배열 + ADX>20)       │
    │ 보합/관망     │ Regime==2 or 혼조                    │
    │ 하락 초기     │ Regime==1 + 이평선 하향              │
    │ 하락/위험     │ Regime==0 or 역배열 + 거래량 증가    │
    └──────────────┴──────────────────────────────────────┘
    """
    regime = regime_info['current']
    transition = regime_info['transition']

    trend_up = bool(last_row.get('Trend_Up', False))
    buy_signal = bool(last_row.get('Final_Buy', last_row.get('Buy_Signal', False)))
    vai_sustained = bool(last_row.get('vai_sustained', False))
    rvol = float(last_row.get('rvol', 0))
    adx = float(last_row.get('adx', 0))

    # ── 판단 로직 ──
    if regime >= 3 and buy_signal and (vai_sustained or rvol > 1.5):
        return '강력매수', True, 5
    elif regime >= 3 or (trend_up and adx > 20):
        return '상승추세', True, 4
    elif regime == 2 or (trend_up and adx <= 20):
        if transition == 'improving':
            return '회복중', True, 3
        else:
            return '보합', False, 2
    elif regime == 1:
        return '하락초기', False, 1
    else:
        return '하락위험', False, 0


# ============================================================
# 차트 생성
# ============================================================

def save_sector_chart(df, ticker, sector_name, status, status_score,
                      folder_path, hurdle, vp, regime_info):
    """섹터 차트 저장 (가격, 이평선, 볼린저, 매수신호, 매물대)"""
    plt = _plt()
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 9),
                                    gridspec_kw={'height_ratios': [3, 1]})

    recent = df.iloc[-120:]  # 최근 6개월

    # ── 상단: 가격 차트 ──
    ax1.plot(recent.index, recent['Close'], label='Price', color='black', linewidth=1.5)
    ax1.plot(recent.index, recent['EMA20'], label='EMA20', color='green', alpha=0.6, linestyle='--')
    ax1.plot(recent.index, recent['EMA60'], label='EMA60', color='orange', alpha=0.6, linestyle='--')
    ax1.fill_between(recent.index, recent['boll_ub'], recent['boll_lb'],
                     alpha=0.1, color='blue', label='BB')

    # 매물대 POC
    if vp:
        ax1.axhline(y=vp['POC_Price'], color='purple', linestyle=':', alpha=0.5,
                    label=f"POC: {vp['POC_Price']:.1f}")

    # 매수 신호
    buy_mask = recent.get('Final_Buy', recent.get('Buy_Signal', pd.Series(False, index=recent.index)))
    buy_idx = recent[buy_mask == True].index
    if not buy_idx.empty:
        ax1.scatter(buy_idx, recent.loc[buy_idx, 'Close'],
                   marker='^', color='red', s=120, zorder=5, label='Buy Signal')

    # 매도 신호
    exit_mask = recent.get('Exit_Signal', pd.Series(False, index=recent.index))
    exit_idx = recent[exit_mask == True].index
    if not exit_idx.empty:
        ax1.scatter(exit_idx, recent.loc[exit_idx, 'Close'],
                   marker='v', color='blue', s=100, zorder=5, label='Exit Signal')

    # 제목
    color_map = {5: 'red', 4: 'orangered', 3: 'darkorange', 2: 'gray', 1: 'steelblue', 0: 'blue'}
    title_color = color_map.get(status_score, 'black')
    hurdle_str = f"{hurdle['Hurdle_Score']:.3f}" if hurdle else 'N/A'
    regime_str = f"R{regime_info['current']}({regime_info['transition']})"

    ax1.set_title(
        f"[{ticker}] {sector_name}  |  {status}  |  {regime_str}  |  Hurdle: {hurdle_str}",
        fontsize=13, fontweight='bold', color=title_color
    )
    ax1.legend(loc='upper left', fontsize=8)
    ax1.grid(True, alpha=0.3)

    # ── 하단: 거래량 차트 ──
    colors = ['green' if c > o else 'red'
              for c, o in zip(recent['Close'], recent['Open'])]
    ax2.bar(recent.index, recent['Volume'], color=colors, alpha=0.6, width=0.8)
    ax2.plot(recent.index, recent['vol_ma20'], color='blue', alpha=0.5, label='Vol MA20')

    # RVOL 주석
    last_rvol = recent['rvol'].iloc[-1]
    ax2.set_title(f"거래량 | RVOL: {last_rvol:.2f}x", fontsize=10)
    ax2.legend(loc='upper left', fontsize=8)
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()

    safe_name = sector_name.split('(')[0].strip().replace('/', '_')
    file_name = f"{ticker}_{safe_name}.png"
    plt.savefig(f"{folder_path}/{file_name}", dpi=100, bbox_inches='tight')
    plt.close()


# ============================================================
# 메인 분석 함수
# ============================================================

def analyze_sectors(market='US', save_charts=True):
    """
    섹터 분석 실행
    
    Parameters:
    -----------
    market : str — 'US' or 'KR'
    save_charts : bool — 차트 저장 여부
    
    Returns:
    --------
    DataFrame — 섹터 상태 요약
    """
    tickers = US_SECTOR_TICKERS if market == 'US' else KR_SECTOR_TICKERS

    os.makedirs(GOOD_DIR, exist_ok=True)
    os.makedirs(BAD_DIR, exist_ok=True)

    print(f"🚀 [섹터 분석] {market} 주요 {len(tickers)}개 산업군 진단 시작...\n")

    try:
        import FinanceDataReader as fdr
    except ImportError:
        print("❌ FinanceDataReader가 필요합니다: pip install finance-datareader")
        return None

    start_date = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')
    results = []

    from tqdm import tqdm
    for ticker, info in tqdm(tickers.items()):
        try:
            # 1. 데이터 다운로드
            df = fdr.DataReader(ticker, start=start_date)
            if df is None or len(df) < 60:
                continue

            # 2. 지표 계산  ★ 통화버그 수정: market 전달 (KR 섹터 ETF 유동성 정규화)
            df = calculate_indicators(df, market=market)
            if df is None or len(df) < 60:
                continue

            # 3. Regime + 전략
            df = add_market_regime(df)
            regime_info = detect_regime_transition(df)

            # 전략 적용 (strategies.py)
            try:
                from stock_auto.strategy.strategies import apply_all_strategies, check_exit_signals
                df = apply_all_strategies(df, regime=regime_info['current'])
                df = check_exit_signals(df)
            except ImportError:
                # Fallback: 기본 전략만
                df['Trend_Up'] = (df['EMA20'] > df['EMA60']) & (df['adx'] > 20)
                df['Buy_Signal'] = df['Trend_Up'] & (df['Signal_Count'] >= 2)
                df['Final_Buy'] = df['Buy_Signal']

            last_row = df.iloc[-1]

            # 4. 매물대
            hurdle = check_immediate_hurdle(df, lookback=40)
            vp = analyze_volume_profile(df, period=120)

            # 5. 상태 진단
            status_label, is_good, status_score = diagnose_sector_status(
                df, last_row, regime_info
            )

            # 6. 차트 저장
            if save_charts:
                target_folder = GOOD_DIR if is_good else BAD_DIR
                save_sector_chart(
                    df, ticker, info['name'], status_label, status_score,
                    target_folder, hurdle, vp, regime_info
                )

            # 7. 결과 수집
            change_1d = round(df['Close'].pct_change().iloc[-1] * 100, 2)
            change_5d = round(df['Close'].pct_change(5).iloc[-1] * 100, 2) if len(df) > 5 else 0
            change_20d = round(df['Close'].pct_change(20).iloc[-1] * 100, 2) if len(df) > 20 else 0

            res = {
                'Ticker': ticker,
                'Sector': info['name'],
                'GICS': info['gics'],
                'Close': round(last_row['Close'], 2),
                'Change_1D(%)': change_1d,
                'Change_5D(%)': change_5d,
                'Change_20D(%)': change_20d,
                'Status': status_label,
                'Status_Score': status_score,
                'Is_Good': is_good,
                'Regime': regime_info['current'],
                'Regime_Trend': regime_info['transition'],
                'Signal_Count': int(last_row.get('Signal_Count', 0)),
                'Effective_Score': round(float(last_row.get('Effective_Score', last_row.get('Composite_Score', 0))), 2),
                'Money_Score': round(float(last_row.get('Money_Score', 0)), 2),
                'Trend_Up': bool(last_row.get('Trend_Up', False)),
                'RVOL': round(float(last_row.get('rvol', 0)), 2),
                'VAI_Sustained': bool(last_row.get('vai_sustained', False)),
                'Liquidity_Score': round(float(last_row.get('liquidity_score', 0)), 2),
                'ADX': round(float(last_row.get('adx', 0)), 1),
                'RSI': round(float(last_row.get('rsi_14', 0)), 1),
                'Hurdle_Score': round(hurdle['Hurdle_Score'], 3) if hurdle else 999,
                'SR_Ratio': round(hurdle['SR_Ratio'], 2) if hurdle else 0,
            }
            results.append(res)

        except Exception as e:
            print(f"  ⚠️ {ticker} 오류: {e}")
            continue

    # ── 결과 저장 ──
    if results:
        df_res = pd.DataFrame(results)
        df_res = df_res.sort_values(by=['Status_Score', 'Effective_Score'],
                                     ascending=[False, False])
        df_res.to_csv(SUMMARY_FILE, index=False, encoding='utf-8-sig')

        good_count = df_res['Is_Good'].sum()
        bad_count = len(df_res) - good_count

        print(f"\n✅ 섹터 분석 완료!")
        print(f"  🟢 Good (상승/매수): {good_count}개")
        print(f"  🔴 Bad  (하락/관망): {bad_count}개")
        print(f"  📂 Good 차트: {GOOD_DIR}")
        print(f"  📂 Bad  차트: {BAD_DIR}")
        print(f"  📄 요약 CSV: {SUMMARY_FILE}")

        # 요약 출력
        print(f"\n{'='*60}")
        for _, row in df_res.iterrows():
            emoji = '🟢' if row['Is_Good'] else '🔴'
            print(f"  {emoji} [{row['Ticker']}] {row['Sector']:<25} "
                  f"| {row['Status']:<6} | R{row['Regime']} "
                  f"| RVOL {row['RVOL']:.1f}x | ADX {row['ADX']:.0f}")
        print(f"{'='*60}")

        # 텔레그램
        _send_telegram(df_res)

        return df_res
    else:
        print("❌ 데이터를 가져오지 못했습니다.")
        return None


def _send_telegram(df):
    """텔레그램 브리핑 전송"""
    try:
        import telegram_msg

        good_df = df[df['Is_Good'] == True]
        bad_df = df[df['Is_Good'] == False]

        msg = f"🌍 **[{TODAY}] 섹터 브리핑**\n\n"
        msg += f"🟢 Good: {len(good_df)}개 | 🔴 Bad: {len(bad_df)}개\n\n"

        if not good_df.empty:
            msg += "**[주목 섹터]**\n"
            for _, r in good_df.head(5).iterrows():
                msg += f"• {r['Sector']} ({r['Ticker']}) — {r['Status']}\n"
                msg += f"  R{r['Regime']} | RVOL {r['RVOL']:.1f}x | 1D {r['Change_1D(%)']:+.1f}%\n"

        if not bad_df.empty:
            msg += "\n**[회피 섹터]**\n"
            for _, r in bad_df.head(3).iterrows():
                msg += f"• {r['Sector']} ({r['Ticker']}) — {r['Status']}\n"

        telegram_msg.send_message(msg)
        telegram_msg.send_file(SUMMARY_FILE, caption="📊 섹터 현황표")
    except Exception:
        pass


# ============================================================
# run_analysis.py 연동 함수들
# ============================================================

def load_sector_status(summary_path=None):
    """
    Sector_Status_Summary.csv를 읽어서 ETF Ticker → Status 매핑 반환
    
    run_analysis.py에서 사용:
        status_map = load_sector_status()
        status = status_map.get('XLK', {}).get('Status', '-')
    """
    if summary_path is None:
        summary_path = SUMMARY_FILE

    # 오늘자 파일이 없으면 최근 파일 탐색
    if not os.path.exists(summary_path):
        base = os.path.dirname(os.path.dirname(summary_path))
        if os.path.exists(base):
            dates = sorted([d for d in os.listdir(base) if os.path.isdir(f"{base}/{d}")],
                          reverse=True)
            for d in dates:
                alt_path = f"{base}/{d}/Sector_Status_Summary.csv"
                if os.path.exists(alt_path):
                    summary_path = alt_path
                    break

    if not os.path.exists(summary_path):
        return {}

    try:
        df = pd.read_csv(summary_path)
        result = {}
        for _, row in df.iterrows():
            result[row['Ticker']] = {
                'Status': row['Status'],
                'Status_Score': row.get('Status_Score', 0),
                'Regime': row.get('Regime', -1),
                'RVOL': row.get('RVOL', 0),
                'Change_1D': row.get('Change_1D(%)', 0),
            }
        return result
    except Exception:
        return {}


def get_sector_status_for_stock(sector_etf_ticker, status_map=None):
    """
    개별 종목의 섹터 ETF 티커로 섹터 상태 조회
    
    Parameters:
    -----------
    sector_etf_ticker : str — 예: 'XLK', 'SOXX'
    status_map : dict or None — load_sector_status() 결과
    
    Returns:
    --------
    str — 상태 문자열 (예: '강력매수', '상승추세', '보합', ...)
    """
    if status_map is None:
        status_map = load_sector_status()

    if not sector_etf_ticker or sector_etf_ticker == '-':
        return '-'

    info = status_map.get(sector_etf_ticker, {})
    return info.get('Status', '-')


# ============================================================
# CLI
# ============================================================

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='섹터 분석')
    parser.add_argument('--market', type=str, default='US', choices=['US', 'KR'])
    parser.add_argument('--no-charts', action='store_true', help='차트 저장 안 함')
    args = parser.parse_args()

    # 폰트 설정 (Windows)
    try:
        plt = _plt()
        plt.rcParams['font.family'] = 'Malgun Gothic'
        plt.rcParams['axes.unicode_minus'] = False
    except Exception:
        pass

    analyze_sectors(market=args.market, save_charts=not args.no_charts)
