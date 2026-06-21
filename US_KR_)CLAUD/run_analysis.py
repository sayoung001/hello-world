"""
run_analysis.py — 통합 분석 실행 스크립트 (v2.1)
=================================================
[v2.1 핵심 변경]
- Effective_Score 도입 (Technical × Liquidity - Penalty)
- Sector_Status 연동 (sector_analysis.py 결과 반영)
- Money/Price 신호 분리 표시
- VAI, Penalty 컬럼 추가

[사용법]
  python run_analysis.py --market US_ALL
  python run_analysis.py --market US_ALL --tickers TSLA NVDA
  python run_analysis.py --market KR_ALL
  python run_analysis.py --market US_ALL --run-sector   # 섹터 분석 먼저 실행
"""

import pandas as pd
import numpy as np
import os
import sys
import glob
from datetime import datetime
from tqdm import tqdm

# 모듈 임포트
from indicators_v2 import (
    calculate_indicators, check_immediate_hurdle, analyze_volume_profile,
    calculate_dynamic_levels
)
from strategies import (
    apply_all_strategies, check_exit_signals,
    get_active_strategies, calculate_strategy_levels,
    ALL_STRATEGIES, MONEY_STRATEGIES
)
from regime_v2 import add_market_regime, detect_regime_transition, get_trading_params
from sector_map import get_sector_etf, INDUSTRY_TO_SECTOR, GICS_SECTOR_ETF
from sector_analysis import load_sector_status, get_sector_status_for_stock

# ============================================================
# 시장별 설정
# ============================================================

MARKET_CONFIG = {
    'US_ALL': {
        'raw_dir': './Raw_Data/NASDAQ',
        'result_dir': './Results/NASDAQ',
        'etf_file': './ETF_Mappings/etf_full_db.csv',
        'sector_map': INDUSTRY_TO_SECTOR,
        'currency': '$',
        'market_label': 'US',
    },
    'KR_ALL': {
        'raw_dir': './Raw_Data/KOSPI',
        'result_dir': './Results/KOSPI',
        'etf_file': './ETF_Mappings/etf_kr_db.csv',
        'sector_map': {},
        'currency': '₩',
        'market_label': 'KR',
    },
}

TODAY = datetime.now().strftime('%Y-%m-%d')


# ============================================================
# 보조 함수
# ============================================================

def load_etf_mappings(etf_file):
    """ETF 매핑 파일 로드"""
    if not os.path.exists(etf_file):
        return {}
    try:
        df = pd.read_csv(etf_file)
        mappings = {}
        for _, row in df.iterrows():
            code = str(row.get('Code', row.get('Ticker', ''))).strip()
            if code:
                mappings[code] = {
                    'Bull_ETF': row.get('Bull_ETF', '-'),
                    'Bear_ETF': row.get('Bear_ETF', '-'),
                }
        return mappings
    except Exception:
        return {}


def load_earnings_calendar():
    """어닝 캘린더 로드"""
    patterns = ['./Raw_Data/Earnings*.csv', './Results/Earnings*.csv']
    for p in patterns:
        files = glob.glob(p)
        if files:
            try:
                df = pd.read_csv(files[0])
                cal = {}
                for _, row in df.iterrows():
                    code = str(row.get('Code', row.get('Ticker', ''))).strip()
                    if code:
                        cal[code] = str(row.get('Earnings_Date', '-'))
                return cal
            except Exception:
                pass
    return {}


def load_backtest_stats():
    """백테스트 결과 로드"""
    patterns = ['./Results/*backtest*.csv', './Results/*Backtest*.csv']
    for p in patterns:
        files = glob.glob(p)
        if files:
            try:
                df = pd.read_csv(files[0])
                stats = {}
                for _, row in df.iterrows():
                    code = str(row.get('Code', row.get('Ticker', ''))).strip()
                    if code:
                        stats[code] = {
                            'WinRate': row.get('WinRate(%)', '-'),
                            'AvgDuration': row.get('AvgDuration', '-'),
                        }
                return stats
            except Exception:
                pass
    return {}


def calc_earnings_dday(earnings_date_str):
    """D-Day 계산"""
    if not earnings_date_str or earnings_date_str == '-':
        return '-'
    try:
        edate = pd.to_datetime(earnings_date_str)
        diff = (edate - pd.Timestamp(TODAY)).days
        if diff < 0:
            return '지남'
        return f'D-{diff}'
    except Exception:
        return '-'


# ============================================================
# 메인 분석
# ============================================================

def run_analysis(market='US_ALL', tickers=None, run_sector=False, use_optuna=False):
    """
    메인 분석 실행
    
    Parameters:
    -----------
    market : str — 'US_ALL' or 'KR_ALL'
    tickers : list or None — 특정 종목만 분석
    run_sector : bool — 섹터 분석 먼저 실행 여부
    use_optuna : bool — Optuna 최적화 사용 여부
    """
    config = MARKET_CONFIG.get(market)
    if not config:
        print(f"❌ 지원하지 않는 시장: {market}")
        return

    print(f"{'='*60}")
    print(f"📊 [{market}] 분석 시작 — {TODAY}")
    print(f"{'='*60}")

    # ── 섹터 분석 실행 (옵션) ──
    if run_sector:
        print("\n🌍 섹터 분석 먼저 실행...")
        try:
            from sector_analysis import analyze_sectors
            analyze_sectors(market=config['market_label'])
        except Exception as e:
            print(f"  ⚠️ 섹터 분석 오류: {e}")

    # ── 섹터 상태 로드 ──
    sector_status_map = load_sector_status()
    if sector_status_map:
        print(f"  ✅ 섹터 상태 로드: {len(sector_status_map)}개")
    else:
        print("  ⚠️ 섹터 상태 없음 (--run-sector로 먼저 실행 권장)")

    # ── 데이터 소스 ──
    raw_dir = config['raw_dir']
    if not os.path.exists(raw_dir):
        print(f"❌ 데이터 폴더 없음: {raw_dir}")
        return

    etf_map = load_etf_mappings(config['etf_file'])
    earnings_cal = load_earnings_calendar()
    bt_stats = load_backtest_stats()
    currency = config['currency']

    # ── CSV 파일 목록 ──
    if tickers:
        csv_files = []
        for t in tickers:
            pattern = f"{raw_dir}/*{t}*.csv"
            csv_files.extend(glob.glob(pattern))
    else:
        csv_files = glob.glob(f"{raw_dir}/*.csv")

    if not csv_files:
        print(f"❌ CSV 파일 없음: {raw_dir}")
        return

    print(f"  📁 대상 종목: {len(csv_files)}개\n")

    # ── 결과 수집 ──
    all_results = []
    buy_results = []
    exit_results = []

    for csv_path in tqdm(csv_files, desc="분석 중"):
        try:
            code = os.path.basename(csv_path).replace('.csv', '').split('_')[0]
            df = pd.read_csv(csv_path)

            # 지표 계산
            df = calculate_indicators(df)
            if df is None or len(df) < 60:
                continue

            # Regime
            df = add_market_regime(df)
            regime_info = detect_regime_transition(df)

            # 전략 적용
            df = apply_all_strategies(df, regime=regime_info['current'])
            df = check_exit_signals(df)

            last = df.iloc[-1]

            # 섹터 정보
            industry = str(last.get('Industry', '-'))
            sector_etf_ticker, sector_name_kr = get_sector_etf(code, industry)
            sector_status = get_sector_status_for_stock(sector_etf_ticker, sector_status_map)

            # ETF 매핑
            etf_info = etf_map.get(code, {'Bull_ETF': '-', 'Bear_ETF': '-'})

            # 어닝
            earnings_date = earnings_cal.get(code, '-')
            dday = calc_earnings_dday(earnings_date)

            # 활성 전략
            active = get_active_strategies(last)
            active_names = ', '.join([s['name'] for s in active]) if active else '-'
            styles = ', '.join(sorted(set(s['style'] for s in active))) if active else '-'
            money_names = [s['name'] for s in active if s['is_money']]

            # 손절/타겟 계산
            if active:
                levels = calculate_strategy_levels(last, active)
            else:
                dl = calculate_dynamic_levels(df, regime=regime_info['current'])
                levels = {
                    'target': dl['target_price'] if dl else None,
                    'stop': dl['stop_price'] if dl else None,
                    'target_pct': dl['target_pct'] if dl else None,
                    'stop_pct': dl['stop_pct'] if dl else None,
                    'rr_ratio': dl['rr_ratio'] if dl else None,
                } if dl else {'target': None, 'stop': None, 'target_pct': None, 'stop_pct': None, 'rr_ratio': None}

            # 매물대
            hurdle = check_immediate_hurdle(df, lookback=40)
            vp = analyze_volume_profile(df, period=120)

            # 백테스트
            bt = bt_stats.get(code, {'WinRate': '-', 'AvgDuration': '-'})

            # 경고
            warnings_list = []
            if last.get('Warning_Count', 0) >= 2:
                warnings_list.append('경고2+')
            if last.get('vai_spike_only', False):
                warnings_list.append('단발거래량')
            if last.get('upper_wick_ratio', 0) > 0.6:
                warnings_list.append('윗꼬리')
            if last.get('Penalty', 0) >= 1.5:
                warnings_list.append(f"P{last['Penalty']:.1f}")
            warning_str = ', '.join(warnings_list) if warnings_list else '-'

            # 레거시 전략 목록 (Buy_Pattern용)
            legacy_signals = []
            for sig_name in ['Trend_Up', 'Momentum', 'Oversold', 'Vol_Explode', 'Vol_Pump', 'Candle_Buy']:
                if last.get(sig_name, False):
                    legacy_signals.append(sig_name)

            # 결과 행
            row_data = {
                'Date': df.index[-1].strftime('%Y-%m-%d') if hasattr(df.index[-1], 'strftime') else str(df.index[-1]),
                'Code': code,
                'Name': last.get('Name', code),
                'Sector': sector_name_kr if sector_name_kr != '-' else industry,
                'Sector_ETF': sector_etf_ticker if sector_etf_ticker else '-',
                'Sector_Status': sector_status,
                'Industry': industry,
                'Earnings_Date': earnings_date,
                'D-Day': dday,
                'Close': round(float(last['Close']), 2),
                'Regime': regime_info['current'],
                'Regime_Trend': regime_info['transition'],
                'Signal_Count': int(last.get('Signal_Count', 0)),
                # ★ 핵심 새 컬럼들
                'Technical_Score': round(float(last.get('Technical_Score', 0)), 2),
                'Money_Score': round(float(last.get('Money_Score', 0)), 2),
                'Price_Score': round(float(last.get('Price_Score', 0)), 2),
                'Liquidity_Score': round(float(last.get('liquidity_score', 0.5)), 2),
                'Penalty': round(float(last.get('Penalty', 0)), 2),
                'Effective_Score': round(float(last.get('Effective_Score', 0)), 2),
                # 기존 호환
                'Composite_Score': round(float(last.get('Effective_Score', 0)), 2),
                'Buy_Signal': bool(last.get('Final_Buy', last.get('Buy_Signal', False))),
                'Exit_Signal': bool(last.get('Exit_Signal', False)),
                'Active_Strategies': active_names,
                'Money_Strategies': ', '.join(money_names) if money_names else '-',
                'Style': styles,
                'Buy_Pattern': ', '.join(legacy_signals) if legacy_signals else '-',
                'Warning': warning_str,
                # ★ VAI 관련
                'VAI_Stage1': round(float(last.get('vai_stage1', 0)), 2),
                'VAI_Stage2': round(float(last.get('vai_stage2', 0)), 2),
                'VAI_Sustained': bool(last.get('vai_sustained', False)),
                'RVOL': round(float(last.get('rvol', 0)), 2),
                # ETF
                'Bull_ETF': etf_info.get('Bull_ETF', '-'),
                'Bear_ETF': etf_info.get('Bear_ETF', '-'),
                # 손절/타겟
                f'Target({currency})': levels.get('target', '-'),
                f'Stop({currency})': levels.get('stop', '-'),
                'Target(%)': levels.get('target_pct', '-'),
                'Stop(%)': levels.get('stop_pct', '-'),
                'R:R': levels.get('rr_ratio', '-'),
                # 매물대
                'Hurdle_Score': round(hurdle['Hurdle_Score'], 3) if hurdle else 999,
                'SR_Ratio': round(hurdle['SR_Ratio'], 2) if hurdle else 0,
                # 백테스트
                'BT_WinRate(%)': bt.get('WinRate', '-'),
                'BT_AvgDuration': bt.get('AvgDuration', '-'),
                # 캔들 페널티
                'Upper_Wick_Ratio': round(float(last.get('upper_wick_ratio', 0)), 3),
                'High_Close_Drop(%)': round(float(last.get('high_close_drop', 0)), 2),
                # 기존 불리언
                'Trend_Up': bool(last.get('Trend_Up', False)),
                'Momentum': bool(last.get('Momentum', False)),
                'Oversold': bool(last.get('Oversold', False)),
                'Vol_Explode': bool(last.get('Vol_Explode', False)),
                'Vol_Pump': bool(last.get('Vol_Pump', False)),
                'Candle_Buy': bool(last.get('Candle_Buy', False)),
            }

            all_results.append(row_data)

            if row_data['Buy_Signal']:
                buy_results.append(row_data)
            if row_data['Exit_Signal']:
                exit_results.append(row_data)

        except Exception as e:
            continue

    # ── 결과 저장 ──
    result_dir = config['result_dir']
    os.makedirs(result_dir, exist_ok=True)

    if all_results:
        df_all = pd.DataFrame(all_results)
        df_all = df_all.sort_values('Effective_Score', ascending=False)
        all_path = f"{result_dir}/{market}_{TODAY}_All_Stocks.csv"
        df_all.to_csv(all_path, index=False, encoding='utf-8-sig')
        print(f"\n📄 전체 결과: {all_path} ({len(df_all)}개)")

    if buy_results:
        df_buy = pd.DataFrame(buy_results)
        df_buy = df_buy.sort_values('Effective_Score', ascending=False)
        buy_path = f"{result_dir}/{market}_{TODAY}_Buy_Signals.csv"
        df_buy.to_csv(buy_path, index=False, encoding='utf-8-sig')
        print(f"🟢 매수 신호: {buy_path} ({len(df_buy)}개)")

        # 상위 5개 요약
        print(f"\n{'='*60}")
        print(f"🏆 TOP 매수 후보")
        print(f"{'='*60}")
        for i, (_, r) in enumerate(df_buy.head(5).iterrows()):
            money_tag = '💰' if r['Money_Score'] > 0 else '📊'
            sector_tag = f"[{r['Sector_Status']}]" if r['Sector_Status'] != '-' else ''
            print(f"  {i+1}. {money_tag} {r['Code']} ({r['Name']})")
            print(f"     Effective: {r['Effective_Score']} "
                  f"(Tech:{r['Technical_Score']} × Liq:{r['Liquidity_Score']} - P:{r['Penalty']})")
            print(f"     Money: {r['Money_Score']} | Price: {r['Price_Score']} "
                  f"| R{r['Regime']}({r['Regime_Trend']})")
            print(f"     VAI: {r['VAI_Stage1']:.1f}/{r['VAI_Stage2']:.1f} "
                  f"{'⚡지속' if r['VAI_Sustained'] else ''} | RVOL: {r['RVOL']:.1f}x")
            print(f"     Sector: {r['Sector']} {sector_tag}")
            print(f"     전략: {r['Active_Strategies']}")
            if r['Warning'] != '-':
                print(f"     ⚠️ {r['Warning']}")
            print()
        print(f"{'='*60}")

        # 텔레그램
        _send_telegram(df_buy, market, currency)

    else:
        print("\n⚪ 매수 신호 없음")

    if exit_results:
        df_exit = pd.DataFrame(exit_results)
        exit_path = f"{result_dir}/{market}_{TODAY}_Exit_Signals.csv"
        df_exit.to_csv(exit_path, index=False, encoding='utf-8-sig')
        print(f"🔴 매도 신호: {exit_path} ({len(df_exit)}개)")

    print(f"\n✅ [{market}] 분석 완료!")


def _send_telegram(df_buy, market, currency):
    """텔레그램 매수 알림 전송"""
    try:
        import telegram_msg

        msg = f"📊 **[{market}] {TODAY} 매수 신호**\n\n"
        msg += f"총 {len(df_buy)}개 | 실효점수 기반\n\n"

        for i, (_, r) in enumerate(df_buy.head(5).iterrows()):
            money_tag = '💰' if r['Money_Score'] > 0 else '📊'
            msg += f"{i+1}. {money_tag} **{r['Code']}** ({r['Name']})\n"
            msg += f"   E:{r['Effective_Score']} (T:{r['Technical_Score']}×L:{r['Liquidity_Score']}-P:{r['Penalty']})\n"
            msg += f"   R{r['Regime']} | {r['Style']} | {r['Active_Strategies']}\n"
            if r['Sector_Status'] != '-':
                msg += f"   섹터: {r['Sector']} [{r['Sector_Status']}]\n"
            target_val = r.get(f'Target({currency})', '-')
            stop_val = r.get(f'Stop({currency})', '-')
            msg += f"   Target: {currency}{target_val} | Stop: {currency}{stop_val}\n\n"

        telegram_msg.send_message(msg)

    except Exception:
        pass


# ============================================================
# CLI
# ============================================================

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='통합 분석 실행')
    parser.add_argument('--market', type=str, default='US_ALL',
                        choices=['US_ALL', 'KR_ALL'], help='시장 선택')
    parser.add_argument('--tickers', nargs='+', default=None,
                        help='특정 종목만 분석 (예: TSLA NVDA)')
    parser.add_argument('--run-sector', action='store_true',
                        help='섹터 분석 먼저 실행')
    parser.add_argument('--optuna', action='store_true',
                        help='Optuna 최적화 사용')
    args = parser.parse_args()

    run_analysis(
        market=args.market,
        tickers=args.tickers,
        run_sector=args.run_sector,
        use_optuna=args.optuna,
    )
