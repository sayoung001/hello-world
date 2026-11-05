"""
자동 운영 데몬 (매일 자동 실행 + 자가보정 피드백).

APScheduler(zoneinfo)로 거래소 현지시각 기준 트리거 → DST 자동. 시간표는 markets.SPECS:
  - US 일일 배치:  KST 09:30 화~토 (미국장 다음날 아침)        STOCK_BATCH_TIME_KST
      ⓪ 라벨링 · 세이브티커 동기화 · 거래소 캐시 · 트리거 일봉 평가
      ① 관측 저장소로 프로파일 자가보정 재구축 ② run_daily → Notion + Telegram
  - KR 일일 배치:  KST 16:10 월~금 (국내장 마감 뒤, 다음 날 개장용)  STOCK_BATCH_TIME_KR_KST
      ENABLE_KR=1 일 때만. 관련 ETF 묶음 포함, 알림은 KR 채널(TELEGRAM_KR_*; 없으면 기본)
  - US 개장(09:30 ET):   US 모니터 스레드(폭주 Telegram + 진입 트리거 장중 감시)
  - KR 개장(09:00 KST):  KR 모니터 스레드(동일, ENABLE_KR=1)

→ 매일 돌수록 관측이 쌓여 프로파일이 실측으로 수렴(정적 frac → 실측 보정).

사용:  .venv/bin/python -m stock_auto.pipeline.run_daemon
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
from stock_auto.notify.telegram import Telegram, surge_alert_callback, for_market
from stock_auto.markets import batch_time_kst, enabled_markets, spec as market_spec

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")


def _batch_time_kst(market: Market = Market.US) -> tuple[int, int]:
    """시장별 배치 시각(KST) — markets.SPECS. (호환용 래퍼)"""
    return batch_time_kst(market)


def _active_markets() -> tuple[Market, ...]:
    """US 집중. KR은 ENABLE_KR=1 일 때만 포함 (markets.enabled_markets)."""
    return enabled_markets()


def _recalibrate_and_batch(markets: tuple[Market, ...] | None = None):
    """일일 배치 + 프로파일 자가보정 재구축. markets 를 주면 그 시장만(시장별 시각에 따로 돈다)."""
    from stock_auto.realtime.profile_builder import save_profile, build_and_save
    from stock_auto.pipeline.daily_batch import run_daily
    from stock_auto.integrations.notion_publisher import NotionPublisher
    from stock_auto.config.clock import now_et
    sec = get_secrets()
    markets = tuple(markets) if markets else _active_markets()
    print(f"[daemon] {now_et():%Y-%m-%d %H:%M %Z} 일일 배치 + 프로파일 재구축 "
          f"({'/'.join(m.value for m in markets)})")

    # ⓪ 결과 라벨링 — 전일까지 쌓인 미라벨 신호에 TP/SL/타임아웃 결과를 붙인다.
    #    이 단계가 없으면 신호만 쌓이고 점수→승률 캘리브레이션이 영원히 갱신되지 않는다.
    try:
        from stock_auto.tracking import labeler
        stat = labeler.label_pending()
        print(f"[daemon] 결과 라벨링: 신규 {stat.get('labeled', 0)}건 · "
              f"대기 {stat.get('pending', 0)}건 · 미체결 {stat.get('skip', 0)}건")
    except Exception as e:  # noqa: BLE001 — 라벨링 실패가 배치를 막지 않게
        print(f"[daemon] 결과 라벨링 실패: {type(e).__name__}: {e}")

    if Market.US in markets:
        _us_chores()
    if Market.KR in markets:
        _kr_chores()

    # ⓪-2 진입 트리거 일봉 평가 — 장중 실시간 감시가 놓친 것을 받아낸다.
    #     RealtimeTriggerWatch(run_monitor)만으로는 구멍이 있다:
    #       · KIS 실시간 등록 상한(약 40종목)을 넘는 종목
    #       · 웹소켓 연결이 끊긴 구간
    #       · 모니터가 안 돌았거나 장외에 조건가를 스친 경우
    #     그러면 '-0,5% 눌림 시 분할 진입' 같은 조건이 실제로 닿았는데도
    #     알림이 안 가고 만료된다 — 아무 일도 안 일어난 것과 구분이 안 된다.
    #     일봉으로 한 번 더 훑어 보수적으로(도달가에 체결) 판정한다.
    try:
        from stock_auto.config.env import get_secrets as _gs
        from stock_auto.tracking.trigger_eval import evaluate_daily, TriggerFirer
        from stock_auto.integrations.notion_publisher import NotionPublisher
        _sec = _gs()
        firer = TriggerFirer(
            notion=NotionPublisher(token=_sec.notion_token) if _sec.has_notion else None,
            db_id=_sec.notion_trigger_db_id or None,
            parent_page=_sec.notion_parent_page_id or None)
        tstat = evaluate_daily(firer=firer)
        print(f"[daemon] 트리거 일봉 평가: 발화 {tstat.get('fired', 0)}건 · "
              f"대기 {tstat.get('pending', 0)}건 · 만료 {tstat.get('expired', 0)}건")
    except Exception as e:  # noqa: BLE001 — 트리거 평가 실패가 배치를 막지 않게
        print(f"[daemon] 트리거 일봉 평가 실패: {type(e).__name__}: {e}")

    for market in markets:
        # 한 시장이 죽어도 다른 시장과 ⑤는 돌아야 하고, 죽었다는 사실은 폰으로 와야 한다.
        # 예전에는 예외가 APScheduler 로그에만 남아 "알림이 안 온다"로만 드러났다.
        try:
            _batch_one_market(market, sec)
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"[daemon] 🚨 {market.value} 일일 배치 실패: {type(e).__name__}: {e}")
            traceback.print_exc()
            _notify_failure(market, sec, e)

    # ⑤ 보유일별 수익 곡선 갱신 + 대시보드 재생성 (배치마다)
    try:
        from stock_auto.tracking import forward, dashboard
        fs = forward.update()
        dp = dashboard.write()
        print(f"[daemon] 보유일 곡선 갱신 {fs['updated']}건 · 대시보드 → {dp}")
    except Exception as e:  # noqa: BLE001
        print(f"[daemon] 보유일 곡선/대시보드 실패: {type(e).__name__}: {e}")


def _batch_one_market(market: Market, sec) -> None:
    """시장 1개의 일일 배치 — 유니버스 → 프로파일 → 섹터 게이트 → (US) 실적 → 추천·게시·알림."""
    from stock_auto.realtime.profile_builder import save_profile, build_and_save
    from stock_auto.pipeline.daily_batch import run_daily
    from stock_auto.integrations.notion_publisher import NotionPublisher

    uni_map = resolve_universe(market)
    syms = list(uni_map.keys())
    print(f"[daemon] {market.value} 유니버스 N={len(syms)}")
    # ① 실측 우선 재구축, 부족분 frac 폴백
    try:
        emp = observation_store.build_profiles_from_observations(market, syms, min_days=10)
        for s, prof in emp.items():
            save_profile(prof, market)
        rest = [s for s in syms if s not in emp]
        if rest:
            build_and_save(rest, market)
        print(f"[daemon] {market.value} 프로파일: 실측 {len(emp)} / frac {len(rest)}")
    except Exception as e:  # noqa: BLE001 — 프로파일(실시간용)은 추천과 무관하다
        print(f"[daemon] {market.value} 프로파일 재구축 실패: {type(e).__name__}: {e}")

    # ② 섹터 게이트 — 하락위험 섹터 종목 매수 차단
    from stock_auto.sector.sector_status import (
        compute_sector_status, labels_for_symbols, summary_lines)
    try:
        sec_scores, sec_labels = compute_sector_status(market)
    except Exception as e:  # noqa: BLE001 — 섹터 실패가 배치를 막지 않게
        print(f"[daemon] 섹터 진단 실패: {type(e).__name__}: {e}")
        sec_scores, sec_labels = {}, {}
    print(f"[daemon] {market.value} 섹터 게이트: {len(sec_scores)}개 진단")

    # ③ 실적 발표일 게이트 — 갭이 손절을 건너뛰는 구간을 진입에서 제외 (미국만)
    earn_blocked: dict = {}
    earn_days: dict = {}
    if market == Market.US:
        try:
            from stock_auto.markets.us import earnings
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
              telegram=for_market(market, sec),     # 국내장은 KR 채널(없으면 기본)
              notion_db_id=sec.notion_reco_db_id or None,
              notion_parent_page=sec.notion_parent_page_id or None,
              run_llm=sec.has_anthropic)


def _notify_failure(market: Market, sec, exc: BaseException) -> None:
    """배치가 죽었음을 그 시장 채널로 알린다. 알림 자체가 실패해도 조용히."""
    try:
        from stock_auto.ui import status as ui_status
        ui_status.record_failure(market.value, exc)       # PC 화면 '배치 실패 기록'
    except Exception:  # noqa: BLE001
        pass
    try:
        tg = for_market(market, sec)
        if getattr(tg, "enabled", False):
            tg.send_message(
                f"🚨 [{market.value}] 일일 배치 실패 — 추천 알림이 오지 않습니다\n"
                f"{type(exc).__name__}: {str(exc)[:300]}\n"
                f"확인: grep -n '배치 실패' -A12 ~/auto_trade/logs/daemon.log | tail -30",
                parse_mode="")
    except Exception:  # noqa: BLE001
        pass


def _us_chores() -> None:
    """미국장 배치 앞 준비 — 세이브티커 동기화, 거래소·회사명 캐시. 실패해도 배치는 간다."""
    # 세이브티커 수집 데이터 동기화 — PC 들이 올린 뉴스를 받아 온다.
    try:
        from stock_auto.markets.us import saveticker_store
        st = saveticker_store.sync()
        mark = "✅" if st["ok"] else "⚠️"
        print(f"[daemon] {mark} 세이브티커 {st['action']}: {st['detail'][:120]}")
        if st["ok"]:
            print(f"[daemon]    {saveticker_store.summary()}")
    except Exception as e:  # noqa: BLE001
        print(f"[daemon] 세이브티커 동기화 실패: {type(e).__name__}: {e}")
    # 미국 종목 거래소·회사명 캐시 — 뉴스 검색어('"Gartner" OR "NYSE: IT"')와
    # 실시간 구독 키(DNYS…)가 쓴다. 7일 캐시라 보통 즉시 끝난다.
    try:
        from stock_auto.markets.us import exchange as us_exchange
        r = us_exchange.refresh()
        print(f"[daemon] 거래소·회사명 목록 {r['action']} ({r.get('n', 0)}종목)"
              + ("" if r["ok"] else f" — {r.get('detail', '')[:80]}"))
    except Exception as e:  # noqa: BLE001
        print(f"[daemon] 거래소·회사명 목록 실패: {type(e).__name__}: {e}")


def _kr_chores() -> None:
    """국내장 배치 앞 준비 — KRX ETF 상장목록 캐시(관련 ETF 묶음용) + 대표 ETF 코드 검증."""
    try:
        from stock_auto.markets.kr import etf as kr_etf
        r = kr_etf.refresh()
        print(f"[daemon] KRX ETF 목록 {r['action']} ({r.get('n', 0)}개)"
              + ("" if r["ok"] else f" — {r.get('detail', '')[:80]}"))
        bad = kr_etf.verify_seeds()
        if bad:
            print("[daemon] ⚠️ 섹터 대표 ETF 코드 불일치 — markets/kr/etf.SECTORS 확인:")
            for b in bad[:8]:
                print(f"[daemon]    · {b}")
    except Exception as e:  # noqa: BLE001
        print(f"[daemon] KRX ETF 목록 실패: {type(e).__name__}: {e}")


def _weekly_report():
    """주 1회(토요일) 성과 요약을 텔레그램으로 — 라벨링·곡선 갱신 후 요약 + 대시보드 파일."""
    from stock_auto.tracking import labeler, forward, store, pnl, dashboard
    from stock_auto.config.clock import now_et
    print(f"[daemon] {now_et():%Y-%m-%d %H:%M %Z} 주간 성과 리포트")
    try:
        labeler.label_pending()
        forward.update()
        rows, fwd_rows = store.load(), forward.load()
        dp = dashboard.write(rows=rows, fwd_rows=fwd_rows)
        sec = get_secrets()
        tg = Telegram(sec.telegram_bot_token, sec.telegram_chat_id)
        if tg.enabled:
            tg.send_message(pnl.telegram_summary(rows, fwd_rows), parse_mode="")
            tg.send_document(str(dp), caption="추천 성과 대시보드 (브라우저로 열기)")
        print("[daemon] 주간 리포트 전송")
    except Exception as e:  # noqa: BLE001
        print(f"[daemon] 주간 리포트 실패: {type(e).__name__}: {e}")


def _trigger_watch(sec, market: Market, tg):
    """pending 트리거 감시기. 실패하면 None — 트리거 때문에 모니터가 죽으면 안 된다."""
    try:
        from stock_auto.tracking.trigger_eval import RealtimeTriggerWatch, TriggerFirer
        from stock_auto.integrations.notion_publisher import NotionPublisher
        firer = TriggerFirer(
            notion=NotionPublisher(token=sec.notion_token) if sec.has_notion else None,
            db_id=sec.notion_trigger_db_id or None,
            parent_page=sec.notion_parent_page_id or None,
            telegram=tg, send_chart=False)          # 장중엔 차트 생략
        tw = RealtimeTriggerWatch(firer=firer, market=market.value)
        if tw.symbols:
            print(f"[daemon] 진입 트리거 감시 {len(tw.symbols)}종목: {tw.symbols[:10]}")
            return tw
        print("[daemon] 진입 트리거 대기 건 없음")
        return None
    except Exception as e:  # noqa: BLE001
        print(f"[daemon] 트리거 감시 비활성({type(e).__name__}: {e})")
        return None


def _trigger_tick(tw):
    """매 스냅샷 → 트리거 판정. 시각은 ISO(오프셋 포함) — Notion 날짜 속성이 받는 형식."""
    def _on_tick(snap):
        try:
            tw.on_price(snap.symbol, float(snap.price),
                        snap.ts.isoformat(timespec="seconds"))
        except Exception as e:  # noqa: BLE001
            print(f"[daemon] 트리거 판정 오류 {snap.symbol}: {type(e).__name__}: {e}")
    return _on_tick


def _exchange_map(market: Market, syms: list[str]) -> dict[str, str]:
    """US 종목별 KIS 거래소 코드. 없으면 NYSE 종목은 체결이 안 온다."""
    if market != Market.US:
        return {}
    try:
        from stock_auto.markets.us import exchange as us_exchange
        r = us_exchange.refresh()
        if not r.get("ok"):
            print(f"[daemon] 거래소 목록 갱신 실패({r.get('detail', '')[:80]}) — "
                  f"캐시 {r.get('n', 0)}종목으로 진행")
        m = us_exchange.load_map(syms)
        print("[daemon] " + us_exchange.summary(syms, m))
        return m
    except Exception as e:  # noqa: BLE001
        print(f"[daemon] 거래소 매핑 실패({type(e).__name__}) — 전부 NAS 로 구독")
        return {}


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
    tg = for_market(market, sec)             # 국내장은 KR 채널(없으면 기본)

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

    # 진입 트리거 장중 감시 — 추천의 '0.5% 눌림' 같은 조건가에 닿는 순간
    # Notion 정리 + Telegram 알림. 매 틱마다 판정한다(on_tick).
    # 예전에는 데몬 스레드에 이 훅이 아예 없었다(CLI run_monitor 에만 있었고,
    # 그마저 on_observation 에 걸려 개장 1·3·5분에만 가격을 봤다). 그래서
    # 조건이 장중에 닿아도 알림이 안 가고, 다음날 아침 일봉 사후점검이
    # 하루 늦게 발화했다.
    tw = _trigger_watch(sec, market, tg)

    monitor = SurgeMonitor(profiles, DEFAULT_THRESHOLDS,
                           on_signal=_on_signal,
                           on_observation=_record,
                           threshold_provider=provider,
                           on_tick=(_trigger_tick(tw) if tw is not None else None))
    deadline = datetime.now() + timedelta(minutes=session_minutes)
    feed = KISFeed(sec.kis_app_key, sec.kis_app_secret, market, env=sec.kis_env,
                   should_stop=lambda: datetime.now() >= deadline,
                   exchange_map=_exchange_map(market, syms))

    def _run():
        print(f"[daemon] {market.value} 모니터 시작 ({session_minutes}분)")
        try:
            monitor.run(feed, syms)
        except Exception as e:  # noqa: BLE001
            print(f"[daemon] {market.value} 모니터 오류: {e}")
        print(f"[daemon] {market.value} 모니터 종료")

    threading.Thread(target=_run, daemon=True).start()


def schedule_lines() -> list[str]:
    """지금 설정으로 무엇이 언제 도는지 — 시작 로그와 --show-schedule 이 같은 걸 보여 준다."""
    from stock_auto.config.env import load_env
    load_env()                                   # ENABLE_KR 등은 .env 에서 온다
    markets = _active_markets()
    sec = get_secrets()
    us = market_spec(Market.US)
    bh, bm = _batch_time_kst(Market.US)
    L = [f"대상시장 {[m.value for m in markets]}",
         f"US 배치 KST {bh:02d}:{bm:02d} {us.batch_days} · 모니터 09:30 ET · "
         f"텔레그램 {'설정' if sec.has_telegram else '미설정'}"]
    if Market.KR in markets:
        kr = market_spec(Market.KR)
        kh, km = _batch_time_kst(Market.KR)
        L.append(f"KR 배치 KST {kh:02d}:{km:02d} {kr.batch_days} · 모니터 09:00 KST · "
                 + ("KR 전용 텔레그램" if sec.has_telegram_kr
                    else "알림은 기본 텔레그램(TELEGRAM_KR_* 미설정)"))
    else:
        L.append("KR 꺼짐 — .env 에 ENABLE_KR=1 을 넣고 데몬을 재시작하면 켜진다")
    return L


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="자동 운영 데몬")
    ap.add_argument("--show-schedule", action="store_true",
                    help="지금 .env 기준으로 어떤 시장이 언제 도는지만 출력하고 종료")
    args = ap.parse_args(argv)

    # .env 를 가장 먼저 — 시장 목록(ENABLE_KR)·배치 시각이 여기서 결정된다.
    # 패키지 import 때도 한 번 올리지만, 명시적으로 다시 보장한다.
    from stock_auto.config.env import load_env
    load_env()

    if args.show_schedule:
        for line in schedule_lines():
            print(line)
        return 0

    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        print("❌ apscheduler 필요: pip install apscheduler")
        return 1

    sch = BlockingScheduler(timezone=ET)   # US 집중 → 기준 TZ를 ET로
    markets = _active_markets()
    # 일일 배치 + 재구축 — **KST 09:30, 화~토** (미국 월~금 장의 다음날 아침)
    # 예전에는 ET 17:00(마감+1h = KST 06:00) 이었다. 옮긴 이유:
    #   세이브티커 뉴스는 사용자 PC 의 브라우저가 수집하는데, PC 가 밤에 꺼져
    #   있으면 미국장 시간대(한국 밤)의 뉴스가 06:00 에는 없다. PC 가 아침에
    #   켜지면 확장이 밤 사이 것을 메우므로, 그 뒤에 돌려야 뉴스가 온전하다.
    #   추천은 다음 미국장 개장(23:30 KST)용이라 09:30 이어도 14시간 남는다.
    # STOCK_BATCH_TIME_KST=HH:MM 으로 바꿀 수 있다.
    us = market_spec(Market.US)
    bh, bm = _batch_time_kst(Market.US)
    sch.add_job(lambda: _recalibrate_and_batch((Market.US,)),
                CronTrigger(hour=bh, minute=bm, day_of_week=us.batch_days, timezone=KST))
    # 주간 성과 요약: 토요일 10:00 KST (한 주 결과 라벨링이 끝난 뒤)
    sch.add_job(_weekly_report,
                CronTrigger(hour=10, minute=0, day_of_week="sat", timezone=KST))
    # US 개장 09:30 ET → 390분 (정규장)
    sch.add_job(lambda: _run_monitor_thread(Market.US, us.session_minutes),
                CronTrigger(hour=us.open_hm[0], minute=us.open_hm[1],
                            day_of_week="mon-fri", timezone=ET))
    sched_lines = [f"US 배치 KST {bh:02d}:{bm:02d} {us.batch_days} · 모니터 09:30 ET"]

    # 국내장 — ENABLE_KR=1 일 때만. 배치는 장 마감 뒤(16:10 KST) 자기 시각에 따로 돈다.
    if Market.KR in markets:
        kr = market_spec(Market.KR)
        kh, km = _batch_time_kst(Market.KR)
        sch.add_job(lambda: _recalibrate_and_batch((Market.KR,)),
                    CronTrigger(hour=kh, minute=km, day_of_week=kr.batch_days, timezone=KST))
        sch.add_job(lambda: _run_monitor_thread(Market.KR, kr.session_minutes),
                    CronTrigger(hour=kr.open_hm[0], minute=kr.open_hm[1],
                                day_of_week="mon-fri", timezone=KST))
        sched_lines.append(f"KR 배치 KST {kh:02d}:{km:02d} {kr.batch_days} · 모니터 09:00 KST"
                           + (" · KR 전용 텔레그램" if get_secrets().has_telegram_kr
                              else " · 알림은 기본 텔레그램(TELEGRAM_KR_* 미설정)"))

    print(f"[daemon] 시작 — 대상시장 {[m.value for m in markets]} | "
          + " | ".join(sched_lines) + " | 자가보정 / 주간성과(토 10:00 KST)")
    print("[daemon] Ctrl+C 로 종료")
    try:
        sch.start()
    except (KeyboardInterrupt, SystemExit):
        print("\n[daemon] 종료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
