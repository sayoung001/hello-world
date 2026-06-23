"""
KIS Open API 실시간 피드 — 거래량 폭주 모니터(volume_monitor) 연결 (Stage 7).

volume_monitor.DataFeed 프로토콜 구현:
  - 국내: TR H0STCNT0 (실시간 체결가)  → KR
  - 해외(미국): TR HDFSCNT0 (해외주식 실시간체결) → US
  WebSocket으로 체결 스트림 수신 → TickSnapshot 변환 → yield.

인증: app_key/app_secret → approval_key(REST) → WS 헤더.
파서는 순수 함수로 분리해 네트워크 없이 검증 가능(테스트).

주의:
- 시세 조회만 사용(주문 권한 불필요).
- 시간대 RVOL/Z는 HistoricalProfile 필요 → 프로파일은 KIS 분봉 이력으로 별도 구축.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Iterator, Optional
from zoneinfo import ZoneInfo

from stock_auto.realtime.volume_monitor import TickSnapshot, Market

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")

# WebSocket 엔드포인트
WS_REAL = "ws://ops.koreainvestment.com:21000"
WS_PAPER = "ws://ops.koreainvestment.com:31000"
REST_REAL = "https://openapi.koreainvestment.com:9443"
REST_PAPER = "https://openapivts.koreainvestment.com:29443"

TR_KR = "H0STCNT0"   # 국내 실시간 체결가
TR_US = "HDFSCNT0"   # 해외 실시간 체결

# 해외 거래소 코드(실시간 tr_key 접두 D + 거래소 + 심볼)
US_EXCH = {"NAS": "NAS", "NYS": "NYS", "AMS": "AMS"}


# ── approval key ───────────────────────────────────────────────────────────
def get_approval_key(app_key: str, app_secret: str, env: str = "real") -> Optional[str]:
    """REST로 WebSocket용 approval_key 발급."""
    base = REST_REAL if env == "real" else REST_PAPER
    try:
        import requests
        r = requests.post(f"{base}/oauth2/Approval",
                          json={"grant_type": "client_credentials",
                                "appkey": app_key, "secretkey": app_secret},
                          timeout=10)
        r.raise_for_status()
        return r.json().get("approval_key")
    except Exception as e:  # noqa: BLE001
        print(f"[kis] approval_key 발급 실패: {type(e).__name__}: {e}")
        return None


def _subscribe_msg(approval_key: str, tr_id: str, tr_key: str) -> str:
    import json
    return json.dumps({
        "header": {"approval_key": approval_key, "custtype": "P",
                   "tr_type": "1", "content-type": "utf-8"},
        "body": {"input": {"tr_id": tr_id, "tr_key": tr_key}},
    })


# ── 필드 인덱스 (KIS 명세) ──────────────────────────────────────────────────
# H0STCNT0(국내): ^ 구분, 종목당 필드
KR_IDX = {"price": 2, "open": 7, "cum_vol": 13, "cum_amt": 14,
          "sign": 3, "vrss": 4, "sell_sum": 19, "buy_sum": 20, "time": 1}
# HDFSCNT0(해외): 종목당 26필드 (실측 검증 2026-06: AAPL HDFSCNT0)
US_IDX = {"open": 8, "price": 11, "sign": 12, "diff": 13,
          "cum_vol": 20, "cum_amt": 21, "sell_vol": 22, "buy_vol": 23,
          "strength": 24, "time": 5}


def _f(x: str) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _prev_close(price: float, vrss: float, sign: str) -> float:
    """전일종가 = 현재가 - 부호 적용 전일대비. sign: 1/2 상승, 4/5 하락."""
    signed = vrss if sign in ("1", "2") else (-vrss if sign in ("4", "5") else 0.0)
    return price - signed


def _hms_to_time(hms: str) -> time:
    """'HHMMSS'(또는 'HHMM') → time. 부족분 0 패딩."""
    s = str(hms).strip().zfill(6)[:6]
    return time(int(s[0:2]), int(s[2:4]), int(s[4:6]))


def _packet_dt_us(ymd: str, hms: str) -> datetime:
    """해외: 현지일자(XYMD 'YYYYMMDD') + 현지시간(XHMS 'HHMMSS') → ET aware datetime."""
    s = str(ymd).strip()
    return datetime(int(s[0:4]), int(s[4:6]), int(s[6:8]),
                    tzinfo=ET).replace(
        hour=_hms_to_time(hms).hour, minute=_hms_to_time(hms).minute,
        second=_hms_to_time(hms).second)


# 해외 현지일자(XYMD) 필드 인덱스
US_XYMD = 4


def parse_kr(fields: list[str], symbol: str) -> TickSnapshot:
    """국내: ts=오늘(KST)+체결시간, session_open=오늘 09:00 KST."""
    i = KR_IDX
    price = _f(fields[i["price"]])
    today = datetime.now(KST).date()
    ts = datetime.combine(today, _hms_to_time(fields[i["time"]]), tzinfo=KST)
    session_open = datetime.combine(today, time(9, 0), tzinfo=KST)
    return TickSnapshot(
        symbol=symbol, market=Market.KR, ts=ts, price=price,
        prev_close=_prev_close(price, _f(fields[i["vrss"]]), fields[i["sign"]]),
        open_price=_f(fields[i["open"]]), session_open=session_open,
        cum_volume=_f(fields[i["cum_vol"]]), cum_turnover=_f(fields[i["cum_amt"]]),
        buy_exec_volume=_f(fields[i["buy_sum"]]),
        sell_exec_volume=_f(fields[i["sell_sum"]]),
    )


def parse_us(fields: list[str], symbol: str) -> TickSnapshot:
    """
    해외: ts·session_open을 '현지(ET) 패킷 시간'으로 산출(서버 시계 의존 제거).
    ts = XYMD(현지일자) + XHMS(현지시간), session_open = 같은 현지일자 09:30 ET.
    실측 검증: 22=매도누적, 23=매수누적 (체결강도 STRN = 매수/매도×100과 일치).
    """
    i = US_IDX
    price = _f(fields[i["price"]])
    ts = _packet_dt_us(fields[US_XYMD], fields[i["time"]])
    session_open = datetime.combine(ts.date(), time(9, 30), tzinfo=ET)
    return TickSnapshot(
        symbol=symbol, market=Market.US, ts=ts, price=price,
        prev_close=_prev_close(price, _f(fields[i["diff"]]), fields[i["sign"]]),
        open_price=_f(fields[i["open"]]), session_open=session_open,
        cum_volume=_f(fields[i["cum_vol"]]), cum_turnover=_f(fields[i["cum_amt"]]),
        buy_exec_volume=_f(fields[i["buy_vol"]]),
        sell_exec_volume=_f(fields[i["sell_vol"]]),
    )


# ── 피드 ────────────────────────────────────────────────────────────────────
class KISFeed:
    """KIS WebSocket → TickSnapshot 스트림. volume_monitor.SurgeMonitor.run에 사용."""

    def __init__(self, app_key: str, app_secret: str, market: Market,
                 env: str = "real", us_exchange: str = "NAS",
                 debug_raw: int = 0, should_stop=None,
                 reconnect: bool = True, recv_timeout: float = 30.0):
        self.app_key = app_key
        self.app_secret = app_secret
        self.market = market
        self.env = env
        self.us_exchange = us_exchange
        self._tr = TR_KR if market == Market.KR else TR_US
        self.debug_raw = debug_raw       # >0이면 첫 N개 원시 패킷을 출력(필드 정렬용)
        self.should_stop = should_stop   # 콜백: True면 스트림 종료(데몬 자동정지)
        self.reconnect = reconnect       # SSH 장기가동: 끊기면 자동 재접속
        self.recv_timeout = recv_timeout  # recv 블로킹 해소(초) → should_stop 반영

    def _stopped(self) -> bool:
        return self.should_stop is not None and self.should_stop()

    def _tr_key(self, symbol: str) -> str:
        if self.market == Market.KR:
            return symbol
        exch = US_EXCH.get(self.us_exchange, "NAS")
        return f"D{exch}{symbol}"

    def stream(self, symbols) -> Iterator[TickSnapshot]:
        """
        WebSocket 체결 스트림. SSH 장기가동 대비:
        - recv_timeout으로 블로킹 해소 → 장 마감/정지 신호를 제때 반영
        - 끊기면 백오프 후 자동 재접속(reconnect=True)
        """
        import time as _time
        try:
            import websocket  # websocket-client
        except ImportError as e:
            raise ImportError("websocket-client 필요: pip install websocket-client") from e

        url = WS_REAL if self.env == "real" else WS_PAPER
        parse = parse_kr if self.market == Market.KR else parse_us
        backoff = 2
        seen = 0

        while not self._stopped():
            approval = get_approval_key(self.app_key, self.app_secret, self.env)
            if not approval:
                if not self.reconnect:
                    return
                _time.sleep(backoff); backoff = min(backoff * 2, 60); continue

            ws = None
            try:
                ws = websocket.create_connection(url, timeout=10)
                ws.settimeout(self.recv_timeout)
                for sym in symbols:
                    ws.send(_subscribe_msg(approval, self._tr, self._tr_key(sym)))
                backoff = 2   # 연결 성공 → 백오프 리셋

                while not self._stopped():
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        continue   # 타임아웃 → 정지신호 재확인 후 계속
                    if not raw:
                        continue
                    if self.debug_raw and seen < self.debug_raw:
                        seen += 1
                        print(f"[kis-raw {seen}] {raw[:600]}")
                    if raw[0] in "{[":
                        if "PINGPONG" in raw:
                            ws.send(raw)
                        continue
                    parts = raw.split("|")
                    if len(parts) < 4:
                        continue
                    tr_id, count, body = parts[1], parts[2], parts[3]
                    if tr_id != self._tr:
                        continue
                    snap = self._parse_body(body, count, parse)
                    if snap:
                        yield snap
            except Exception as e:  # noqa: BLE001 — 끊김/오류 → 재접속
                print(f"[kis] 연결 끊김/오류: {type(e).__name__}: {e}")
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:  # noqa: BLE001
                        pass
            if not self.reconnect or self._stopped():
                break
            print(f"[kis] {backoff}s 후 재접속...")
            _time.sleep(backoff); backoff = min(backoff * 2, 60)

    def _parse_body(self, body: str, count: str, parse):
        """다건 패킷(count>1)은 종목당 필드수로 나눠 '최신(마지막)' 레코드만 파싱."""
        fields = body.split("^")
        try:
            n = max(int(count), 1)
        except (TypeError, ValueError):
            n = 1
        rec_size = len(fields) // n
        if rec_size < 10:
            return None
        rec = fields[-rec_size:]   # 가장 최근(누적값이 최신) 레코드
        # 종목코드: KR=rec[0], US=rec[1](SYMB)
        symbol = rec[0] if self.market == Market.KR else rec[1]
        try:
            return parse(rec, symbol)   # ts/session_open은 패킷 시간으로 산출
        except (IndexError, ValueError):
            return None
