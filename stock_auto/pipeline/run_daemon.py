"""
자동 운영 데몬 (매일 자동 실행 + 자가보정 피드백).

APScheduler(zoneinfo)로 거래소 현지시각 기준 트리거 → DST 자동:
  - 일일 배치 + 프로파일 재구축:  US 마감(16:00 ET)+1h
      ① 관측 저장소(전일 실시간 누적)로 프로파일 자가보정 재구축(--from-observations)
      ② run_daily(US/KR) → 추천 → Notion 게시(아카이브) + Telegram 다이제스트(푸시)
  - KR 개장(09:00 KST):  KR 모니터 스레드 시작(장중 누적 관측 적립 + 폭주 Telegram)
  - US 개장(09:30 ET):   US 모니터 스레드 시작(동일)

→ 매일 돌수록 관측이 쌓여 프로파일이 실측으로 수렴(정적 frac → 실측 보정).

사용:  python -m stock_auto.pipeline.run_daemon
정지:  Ctrl+C
주의: 단일 프로세스 데몬. 운영 안정성이 중요하면 배치/모니터를 별 프로세스(cron/systemd)로
      분리하고 본 모듈은 참고 구현으로 사용.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from stock_auto.config.env import get_secrets
from stock_auto.config.settings import Market
from stock_auto.config.universe import resolve_universe
from stock_auto.realtime.volume_monitor import (
    SurgeMonitor, HistoricalProfile, DEFAULT_THRESHOLDS)
from stock_auto.realtime import observation_store
from stock_auto.data.kis_feed import KISFeed
from stock_auto.notify.telegram import Telegram, surge_alert_callback

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")


def _active_markets() -> tuple[Market, ...]:
    """US 집중. KR은 확장성용 — 환경변수 ENABLE_KR=1 일 때만 포함."""
    if os.environ.get("ENABLE_KR", "").strip() in ("1", "true", "True"):
        return (Market.US, Market.KR)
    return (Market.US,)


def _recalibrate_and_batch():
    """일일 배치 + 프로파일 자가보정 재구축."""
    from stock_auto.realtime.profile_builder import save_profile, build_and_save
    from stock_auto.pipeline.daily_batch import run_daily
    from stock_auto.integrations.notion_publisher import NotionPublisher
    from stock_auto.config.clock import now_et
    sec = get_secrets()
    print(f"[daemon] {now_et():%Y-%m-%d %H:%M %Z} 일일 배치 + 프로파일 재구축")

    # ⓪ 결과 라벨링 — 전일까지 쌓인 미라벨 신호에 TP/SL/타임아웃 결과를 붙인다.
    #    이 단계가 없으면 신호만 쌓이고 점수→승률 캘리브레이션이 영원히 갱신되지 않는다.
    try:
        from stock_auto.tracking import labeler, store
        stat = labeler.label_pending(store.DEFAULT_PATH)
        print(f"[daemon] 결과 라벨링: 신규 {stat.get('labeled', 0)}건 · "
              f"대기 {stat.get('pending', 0)}건 · 미체결 {stat.get('skip', 0)}건")
    except Exception as e:  # noqa: BLE001 — 라벨링 실패가 배치를 막지 않게
        print(f"[daemon] 결과 라벨링 실패: {type(e).__name__}: {e}")

    for market in _active_markets():
        uni_map = resolve_universe(market)
        syms = list(uni_map.keys())
        print(f"[daemon] {market.value} 유니버스 N={len(syms)}")
        # ① 실측 우선 재구축, 부족분 frac 폴백
        emp = observation_store.build_profiles_from_observations(market, syms, min_days=10)
        for s, prof in emp.items():
            save_profile(prof, market)
        rest = [s for s in syms if s not in emp]
        if rest:
            build_and_save(rest, market)
        print(f"[daemon] {market.value} 프로파일: 실측 {len(emp)} / frac {len(rest)}")

        # ② 섹터 게이트 — 하락위험 섹터 종목 매수 차단
        from stock_auto.sector.sector_status import (
            compute_sector_status, labels_for_symbols, summary_lines)
        try:
            sec_scores, sec_labels = compute_sector_status(market)
        except Exception as e:  # noqa: BLE001 — 섹터 실패가 배치를 막지 않게
            print(f"[daemon] 섹터 진단 실패: {type(e).__name__}: {e}")
            sec_scores, sec_labels = {}, {}
        print(f"[daemon] {market.value} 섹터 게이트: {len(sec_scores)}개 진단")

        # ③ 실적 발표일 게이트 — 갭이 손절을 건너뛰는 구간을 진입에서 제외
        earn_blocked: dict = {}
        earn_days: dict = {}
        if market == Market.US:
            try:
                from stock_auto.data import earnings
                earnings.refresh(syms)
                earn_blocked, earn_days = earnings.blocked_map(syms)
                print("[daemon] " + earnings.summary_line(earn_blocked, earn_days))
            except Exception as e:  # noqa: BLE001 — 실적 수집 실패가 배치를 막지 않게
                print(f"[daemon] 실적 캘린더 실패: {type(e).__name__}: {e}")

        # ④ 추천 배치
        notion = NotionPublisher(token=sec.notion_token) if sec.has_notion else None
        run_daily(market=market, universe=syms,
                  stock_sector_etf=uni_map,
                  earnings_blocked=earn_blocked or None,
                  earnings_days=earn_days or None,
                  sector_status=sec_scores or None,
                  sector_label_map=(labels_for_symbols(uni_map, sec_labels)
                                    if sec_labels else None),
                  sector_lines=(summary_lines(sec_scores, sec_labels, market)
                                if sec_scores else None),
                  notion=notion, send_telegram=True,
                  notion_db_id=sec.notion_reco_db_id or None,
                  notion_parent_page=sec.notion_parent_page_id or None,
                  run_llm=sec.has_anthropic)


def _run_monitor_thread(market: Market, session_minutes: int):
    """개장 시 모니터를 스레드로 구동, 장 마감까지 자동 정지."""
    sec = get_secrets()
    if not sec.has_kis:
        print("[daemon] KIS 키 없음 — 모니터 생략")
        return
    # 실시간은 KIS 등록 건수 제한이 있다 — 유니버스 전체가 아니라
    # 보유·직전 후보 우선으로 추린 감시 목록만 구독한다.
    from stock_auto.realtime.watchlist import build as build_watchlist
    syms = build_watchlist(market, list(resolve_universe(market).keys()))
    print(f"[daemon] {market.value} 실시간 감시 {len(syms)}종목")

    # 프로파일 로드
    from stock_auto.pipeline.run_monitor import _load_profiles
    profiles = _load_profiles(market, syms)
    tg = Telegram(sec.telegram_bot_token, sec.telegram_chat_id)

    def _record(snap, window):
        # 날짜는 스냅샷 거래소시각(ET) 기준 — 서버 TZ 무관
        observation_store.record(snap.market, snap.ts.strftime("%Y-%m-%d"),
                                 snap.symbol, window, snap.cum_volume, snap.cum_turnover)

    # 적응형 임계 — 실측 RVOL 분포의 상위 분위수. 표본 부족 시 고정 임계 폴백
    try:
        from stock_auto.realtime.adaptive_thresholds import build as build_at
        at = build_at(market, profiles)
        print("[daemon] " + at.summary())
        provider = at.provider()
    except Exception as e:  # noqa: BLE001 — 임계 산출 실패가 모니터를 막지 않게
        print(f"[daemon] 적응 임계 산출 실패({type(e).__name__}) → 고정 임계 사용")
        provider = None

    # 알림도 결과 라벨링 대상 — 정밀도 측정을 위해 전 건 기록
    from stock_auto.tracking import store as sig_store
    tg_send = (surge_alert_callback(tg) if tg.enabled
               else (lambda s: print(s.to_telegram())))

    def _on_signal(sig):
        tg_send(sig)
        try:
            sig_store.append([sig_store.from_surge_signal(
                sig, sig.ts.strftime("%Y-%m-%d"))])
        except Exception as e:  # noqa: BLE001
            print(f"[daemon] 알림 기록 실패: {type(e).__name__}: {e}")

    monitor = SurgeMonitor(profiles, DEFAULT_THRESHOLDS,
                           on_signal=_on_signal,
                           on_observation=_record,
                           threshold_provider=provider)
    deadline = datetime.now() + timedelta(minutes=session_minutes)
    feed = KISFeed(sec.kis_app_key, sec.kis_app_secret, market, env=sec.kis_env,
                   should_stop=lambda: datetime.now() >= deadline)

    def _run():
        print(f"[daemon] {market.value} 모니터 시작 ({session_minutes}분)")
        try:
            monitor.run(feed, syms)
        except Exception as e:  # noqa: BLE001
            print(f"[daemon] {market.value} 모니터 오류: {e}")
        print(f"[daemon] {market.value} 모니터 종료")

    threading.Thread(target=_run, daemon=True).start()


def main() -> int:
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        print("❌ apscheduler 필요: pip install apscheduler")
        return 1

    sch = BlockingScheduler(timezone=ET)   # US 집중 → 기준 TZ를 ET로
    markets = _active_markets()
    # 일일 배치 + 재구축: ET 17:00(마감+1h), DST 자동
    sch.add_job(_recalibrate_and_batch,
                CronTrigger(hour=17, minute=0, day_of_week="mon-fri", timezone=ET))
    # US 개장 09:30 ET → 약 390분 (정규장)
    sch.add_job(lambda: _run_monitor_thread(Market.US, 390),
                CronTrigger(hour=9, minute=30, day_of_week="mon-fri", timezone=ET))
    # KR 개장(확장성): ENABLE_KR=1 일 때만
    if Market.KR in markets:
        sch.add_job(lambda: _run_monitor_thread(Market.KR, 390),
                    CronTrigger(hour=9, minute=0, day_of_week="mon-fri", timezone=KST))

    print(f"[daemon] 시작 — 대상시장 {[m.value for m in markets]} "
          "| 일일배치(US마감+1h) / 개장 모니터 / 자가보정")
    print("[daemon] Ctrl+C 로 종료")
    try:
        sch.start()
    except (KeyboardInterrupt, SystemExit):
        print("\n[daemon] 종료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
