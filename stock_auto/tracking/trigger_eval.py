"""
트리거 평가 — pending 트리거를 가격과 맞춰 발화·만료시킨다.

두 경로로 돈다.
  1. 일봉 사후점검 (check_triggers CLI / 배치)
     당일 저가·고가로 판정한다. 장중에 못 봤어도 "오늘 닿았다"를 놓치지 않는다.
  2. 장중 실시간 (realtime 모니터 훅)
     KIS 스냅샷 가격으로 즉시 판정한다. 닿는 순간 알림이 간다.

둘 다 같은 저장소를 쓰고 `status`로 중복 발화를 막는다.
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from stock_auto.tracking import triggers as T
from stock_auto.tracking import trigger_notify as N


class TriggerFirer:
    """발화 처리(Notion 게시 + Telegram 알림)를 한 곳에 모은다."""

    def __init__(self, notion: Any = None, db_id: Optional[str] = None,
                 parent_page: Optional[str] = None, telegram: Any = None,
                 send_chart: bool = True):
        self.notion, self.db_id = notion, db_id
        self.parent_page, self.telegram = parent_page, telegram
        self.send_chart = send_chart

    def _tg(self):
        if self.telegram is not None:
            return self.telegram
        from stock_auto.config.env import get_secrets
        from stock_auto.notify.telegram import Telegram
        sec = get_secrets()
        self.telegram = Telegram(sec.telegram_bot_token, sec.telegram_chat_id)
        return self.telegram

    def fire(self, row: dict, price: float, when: str,
             df: Optional[pd.DataFrame] = None) -> dict:
        """트리거 1건 발화. row를 제자리에서 갱신해 돌려준다."""
        chart_note = N.chart_note_from(df) if df is not None else ""
        notion_url = ""

        # 1) Notion 정리 — 먼저 한다. 링크를 알림에 담아야 하므로
        if self.notion is not None and getattr(self.notion, "enabled", False):
            try:
                if self.db_id:
                    props = N.trigger_row(row, price, when, chart_note)
                    pid = self.notion.add_row(self.db_id, props)
                    if pid:
                        notion_url = f"https://www.notion.so/{str(pid).replace('-', '')}"
                elif self.parent_page:
                    blocks = N.trigger_page_blocks(row, price, chart_note)
                    pid = self.notion.create_summary_page(
                        self.parent_page,
                        f"🎯 {row.get('symbol')} 진입 도달 {when[:10]}", blocks)
                    if pid:
                        notion_url = f"https://www.notion.so/{str(pid).replace('-', '')}"
            except Exception as e:  # noqa: BLE001 — 게시 실패가 알림을 막지 않게
                print(f"[trigger] Notion 정리 실패 {row.get('symbol')}: "
                      f"{type(e).__name__}: {e}")

        # 2) Telegram 단축 알림 (+ 차트)
        tg = self._tg()
        msg = N.short_alert(row, price, notion_url)
        if getattr(tg, "enabled", False):
            tg.send_message(msg, parse_mode="")
            if self.send_chart and df is not None:
                try:
                    from stock_auto.notify.chart import render_signal_chart
                    plan = {"stop": T._f(row.get("stop")),
                            "targets": [T._f(row.get("target"))],
                            "close": price, "recommend": row.get("recommend"),
                            "horizon": row.get("horizon"),
                            "conviction": T._f(row.get("conviction"))}
                    png = render_signal_chart(str(row.get("symbol")), df, plan)
                    tg.send_photo(png, f"{row.get('symbol')} 진입 도달",
                                  filename=f"{row.get('symbol')}_trigger.png")
                except Exception as e:  # noqa: BLE001
                    print(f"[trigger] 차트 전송 생략: {type(e).__name__}")
        else:
            print("[trigger] 텔레그램 미설정 — 콘솔 출력\n" + msg)

        row["status"] = "fired"
        row["fired_at"] = when
        row["fired_price"] = price
        row["notion_url"] = notion_url
        row["notify_at"] = when
        return row


# ── 일봉 사후점검 ──────────────────────────────────────────────────────────
def evaluate_daily(ohlcv_map: Optional[dict[str, pd.DataFrame]] = None,
                   base: Optional[str] = None,
                   firer: Optional[TriggerFirer] = None,
                   today: Optional[str] = None) -> dict[str, int]:
    """
    pending 트리거를 일봉으로 판정한다.

    - 추천일 **다음** 거래일부터 본다(추천일 당일 저가로 발화하면 look-ahead).
    - horizon 유효기간을 넘긴 건은 expired.
    - 반환: {'fired': n, 'expired': n, 'pending': n, 'nodata': n}
    """
    from stock_auto.config.clock import now_et
    from stock_auto.config.settings import Market

    rows = T.load(base)
    todo = [r for r in rows if r.get("status") == "pending"]
    stat = {"fired": 0, "expired": 0, "pending": 0, "nodata": 0}
    if not todo:
        return stat

    now = now_et()
    today = today or now.strftime("%Y-%m-%d")
    firer = firer or TriggerFirer()

    # 일봉 확보
    if ohlcv_map is None:
        from datetime import timedelta
        from stock_auto.data.downloader import load_or_download
        ohlcv_map = {}
        per_market = {r["symbol"]: r.get("market") or "US" for r in todo}
        earliest = min(pd.Timestamp(r["date"]) for r in todo)
        start = (earliest - timedelta(days=30)).strftime("%Y-%m-%d")
        for s in sorted(per_market):
            try:
                ohlcv_map[s] = load_or_download(
                    s, start, None, Market(per_market[s]))
            except Exception as e:  # noqa: BLE001
                print(f"[trigger] {s} 일봉 실패: {type(e).__name__}: {e}")

    for r in todo:
        df = ohlcv_map.get(r["symbol"])
        if df is None or df.empty:
            stat["nodata"] += 1
            continue
        sig = pd.Timestamp(r["date"])
        fwd = df[df.index > sig]              # 추천일 다음 거래일부터
        if fwd.empty:
            stat["pending"] += 1
            continue

        limit = T.VALID_DAYS.get(str(r.get("horizon", "")), T.DEFAULT_VALID_DAYS)
        window = fwd.iloc[:limit]
        kind = str(r.get("kind", ""))
        tgt = T._f(r.get("target_price"))

        hit_idx = None
        for ts, bar in window.iterrows():
            if T.reached(kind, tgt, float(bar["Low"]), float(bar["High"])):
                hit_idx = ts
                break

        if hit_idx is not None:
            # 체결가는 보수적으로: pullback은 목표가(그 아래로 갔으므로 체결),
            # breakout도 목표가. 갭으로 더 유리하게 열렸어도 목표가로 본다.
            price = tgt if tgt is not None else float(window.loc[hit_idx, "Close"])
            when = pd.Timestamp(hit_idx).strftime("%Y-%m-%d")
            firer.fire(r, float(price), when, df.loc[:hit_idx])
            stat["fired"] += 1
        elif len(fwd) > limit:
            # 만료는 발화가 아니므로 fired_* 는 비워 둔다.
            # 처리 시각만 notify_at 에 남긴다(나중에 CSV를 읽을 때 헷갈리지 않게).
            r["status"] = "expired"
            r["notify_at"] = pd.Timestamp(fwd.index[limit - 1]).strftime("%Y-%m-%d")
            stat["expired"] += 1
        else:
            stat["pending"] += 1

    T.save_all(rows, base)
    return stat


# ── 장중 실시간 훅 ─────────────────────────────────────────────────────────
class RealtimeTriggerWatch:
    """
    모니터의 스냅샷마다 호출해 pending 트리거를 즉시 판정한다.

    SurgeMonitor(on_observation=...) 와 함께 쓰거나, 피드 루프에서 직접 부른다.
    pending 목록은 메모리에 올려 두고(장중엔 거의 바뀌지 않는다) 발화 시에만 저장한다.
    """

    def __init__(self, base: Optional[str] = None,
                 firer: Optional[TriggerFirer] = None,
                 market: Optional[str] = None):
        self.base = base
        self.firer = firer or TriggerFirer(send_chart=False)   # 장중엔 차트 생략
        self.rows = T.load(base)
        self.index: dict[str, list[dict]] = {}
        for r in self.rows:
            if r.get("status") != "pending":
                continue
            if market and r.get("market") != market:
                continue
            self.index.setdefault(r["symbol"], []).append(r)

    @property
    def symbols(self) -> list[str]:
        return list(self.index)

    def on_price(self, symbol: str, price: float, when: str,
                 df: Optional[pd.DataFrame] = None) -> int:
        """가격 1건으로 판정. 발화 건수를 돌려준다."""
        cands = self.index.get(symbol)
        if not cands:
            return 0
        fired = 0
        for r in list(cands):
            if r.get("status") != "pending":
                continue
            kind = str(r.get("kind", ""))
            tgt = T._f(r.get("target_price"))
            # 실시간은 현재가 1점이므로 저가·고가 모두 현재가로 본다
            if T.reached(kind, tgt, price, price):
                self.firer.fire(r, float(price), when, df)
                fired += 1
        if fired:
            T.save_all(self.rows, self.base)
            self.index[symbol] = [r for r in cands if r.get("status") == "pending"]
            if not self.index[symbol]:
                del self.index[symbol]
        return fired
