"""
스케줄러 — 미장/국장 분할 (사용자 정책: US/KR 스케줄러 잘 분리).

DST(서머타임) 정확성을 위해 stdlib zoneinfo 사용 → 거래소 현지시각 기준으로 계산하고
KST로 변환한다(분석 §9 캘린더/DST 보완).

트리거(KST):
  - 일일 배치:   US 마감(16:00 ET) + 1시간  → KST 약 06:00~07:00 (DST 따라 변동)
  - KR 개장 모니터: 09:00 KST
  - US 개장 모니터: 09:30 ET → KST 23:30(표준) / 22:30(DST)

휴장일: 간이 주말 체크 + 사용자 지정 휴장일 set. 정밀 캘린더는 exchange_calendars 사용 권장.
실제 구동은 cron/APScheduler 권장 — 본 모듈은 트리거 시각 계산 + 경량 루프를 제공한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")

US_OPEN_ET = time(9, 30)
US_CLOSE_ET = time(16, 0)
KR_OPEN_KST = time(9, 0)


def _is_business_day(d: datetime, holidays: Optional[set[str]] = None) -> bool:
    if d.weekday() >= 5:  # 토(5)/일(6)
        return False
    if holidays and d.strftime("%Y-%m-%d") in holidays:
        return False
    return True


def daily_batch_trigger_kst(now: Optional[datetime] = None,
                            holidays: Optional[set[str]] = None) -> datetime:
    """
    다음 일일 배치 시각(KST) = 직전/당일 US 마감 +1시간을 KST로.
    US 영업일 16:00 ET + 1h 를 계산해 KST로 변환.
    """
    now = now or datetime.now(KST)
    now_et = now.astimezone(ET)
    # 오늘 ET 마감+1h
    cand_et = datetime.combine(now_et.date(), US_CLOSE_ET, tzinfo=ET) + timedelta(hours=1)
    # 영업일 아니거나 이미 지났으면 다음 영업일로
    while (not _is_business_day(cand_et, holidays)) or cand_et <= now_et:
        cand_et += timedelta(days=1)
        cand_et = datetime.combine(cand_et.date(), US_CLOSE_ET, tzinfo=ET) + timedelta(hours=1)
        if _is_business_day(cand_et, holidays) and cand_et > now_et:
            break
    return cand_et.astimezone(KST)


def kr_open_trigger_kst(now: Optional[datetime] = None,
                        holidays: Optional[set[str]] = None) -> datetime:
    """다음 KR 개장(09:00 KST)."""
    now = now or datetime.now(KST)
    cand = datetime.combine(now.date(), KR_OPEN_KST, tzinfo=KST)
    while (not _is_business_day(cand, holidays)) or cand <= now:
        cand = datetime.combine((cand + timedelta(days=1)).date(), KR_OPEN_KST, tzinfo=KST)
    return cand


def us_open_trigger_kst(now: Optional[datetime] = None,
                        holidays: Optional[set[str]] = None) -> datetime:
    """다음 US 개장(09:30 ET) → KST. DST 자동 반영."""
    now = now or datetime.now(KST)
    now_et = now.astimezone(ET)
    cand_et = datetime.combine(now_et.date(), US_OPEN_ET, tzinfo=ET)
    while (not _is_business_day(cand_et, holidays)) or cand_et <= now_et:
        cand_et = datetime.combine((cand_et + timedelta(days=1)).date(),
                                   US_OPEN_ET, tzinfo=ET)
    return cand_et.astimezone(KST)


@dataclass
class Schedule:
    """다음 트리거 시각 요약(KST)."""
    daily_batch: datetime
    kr_open: datetime
    us_open: datetime

    def as_dict(self) -> dict[str, str]:
        f = "%Y-%m-%d %H:%M %Z"
        return {"daily_batch(KST)": self.daily_batch.strftime(f),
                "kr_open(KST)": self.kr_open.strftime(f),
                "us_open(KST)": self.us_open.strftime(f)}


def next_triggers(now: Optional[datetime] = None,
                  holidays: Optional[set[str]] = None) -> Schedule:
    return Schedule(
        daily_batch=daily_batch_trigger_kst(now, holidays),
        kr_open=kr_open_trigger_kst(now, holidays),
        us_open=us_open_trigger_kst(now, holidays),
    )


# ── cron 라인 예시 (프로덕션 권장) ──────────────────────────────────────────
CRON_HINTS = """\
# crontab (서버 TZ=Asia/Seoul 가정). DST로 US 트리거는 ±1h 흔들리므로
# 가능하면 APScheduler(zoneinfo)로 동적 계산 권장.
# 일일 배치: US 마감+1h ≈ 06:00~07:00 KST (표준시 07:00, DST 06:00)
0 6 * * 1-5   python -m stock_auto.pipeline.run_daily_batch
# KR 개장 모니터
0 9 * * 1-5   python -m stock_auto.pipeline.run_kr_monitor
# US 개장 모니터: 23:30 KST(표준) / 22:30(DST) — 둘 다 등록하고 내부에서 게이트
30 23 * * 1-5 python -m stock_auto.pipeline.run_us_monitor
30 22 * * 1-5 python -m stock_auto.pipeline.run_us_monitor
"""


def run_apscheduler(on_daily_batch: Callable, on_kr_open: Callable,
                    on_us_open: Callable, holidays: Optional[set[str]] = None):
    """
    APScheduler 기반 구동(설치 시). 현지시각 기준 cron 트리거 → DST 자동 처리.
    미설치 시 ImportError로 안내.
    """
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as e:
        raise ImportError("apscheduler 필요: pip install apscheduler") from e

    sch = BlockingScheduler(timezone=KST)
    # 일일 배치: ET 17:00(마감+1h)에 맞춰 ET 기준 cron → DST 자동
    sch.add_job(on_daily_batch, CronTrigger(hour=17, minute=0,
                day_of_week="mon-fri", timezone=ET))
    # KR 개장 09:00 KST
    sch.add_job(on_kr_open, CronTrigger(hour=9, minute=0,
                day_of_week="mon-fri", timezone=KST))
    # US 개장 09:30 ET (DST 자동)
    sch.add_job(on_us_open, CronTrigger(hour=9, minute=30,
                day_of_week="mon-fri", timezone=ET))
    print("[scheduler] APScheduler 시작 (Ctrl+C 종료)")
    sch.start()
