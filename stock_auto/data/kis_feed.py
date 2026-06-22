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
# HDFSCNT0(해외): 종목당 필드
US_IDX = {"open": 8, "price": 11, "sign": 12, "diff": 13,
          "cum_vol": 20, "cum_amt": 21, "strength": 24, "time": 5}


def _f(x: str) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _prev_close(price: float, vrss: float, sign: str) -> float:
    """전일종가 = 현재가 - 부호 적용 전일대비. sign: 1/2 상승, 4/5 하락."""
    signed = vrss if sign in ("1", "2") else (-vrss if sign in ("4", "5") else 0.0)
    return price - signed


def parse_kr(fields: list[str], symbol: str, session_open: datetime,
             ts: datetime) -> TickSnapshot:
    i = KR_IDX
    price = _f(fields[i["price"]])
    return TickSnapshot(
        symbol=symbol, market=Market.KR, ts=ts, price=price,
        prev_close=_prev_close(price, _f(fields[i["vrss"]]), fields[i["sign"]]),
        open_price=_f(fields[i["open"]]), session_open=session_open,
        cum_volume=_f(fields[i["cum_vol"]]), cum_turnover=_f(fields[i["cum_amt"]]),
        buy_exec_volume=_f(fields[i["buy_sum"]]),
        sell_exec_volume=_f(fields[i["sell_sum"]]),
    )


def parse_us(fields: list[str], symbol: str, session_open: datetime,
             ts: datetime) -> TickSnapshot:
    i = US_IDX
    price = _f(fields[i["price"]])
    # 해외는 매수/매도 체결량 분리 미제공 → 체결강도(STRN, 매수/매도*100)를 비율로 인코딩
    strn = _f(fields[i["strength"]])
    return TickSnapshot(
        symbol=symbol, market=Market.US, ts=ts, price=price,
        prev_close=_prev_close(price, _f(fields[i["diff"]]), fields[i["sign"]]),
        open_price=_f(fields[i["open"]]), session_open=session_open,
        cum_volume=_f(fields[i["cum_vol"]]), cum_turnover=_f(fields[i["cum_amt"]]),
        buy_exec_volume=strn, sell_exec_volume=100.0,  # strength = strn/100
    )


def _session_open(market: Market, now: Optional[datetime] = None) -> datetime:
    if market == Market.KR:
        now = now or datetime.now(KST)
        return datetime.combine(now.date(), time(9, 0), tzinfo=KST)
    now = now or datetime.now(ET)
    return datetime.combine(now.date(), time(9, 30), tzinfo=ET)


# ── 피드 ────────────────────────────────────────────────────────────────────
class KISFeed:
    """KIS WebSocket → TickSnapshot 스트림. volume_monitor.SurgeMonitor.run에 사용."""

    def __init__(self, app_key: str, app_secret: str, market: Market,
                 env: str = "real", us_exchange: str = "NAS",
                 debug_raw: int = 0, should_stop=None):
        self.app_key = app_key
        self.app_secret = app_secret
        self.market = market
        self.env = env
        self.us_exchange = us_exchange
        self._tr = TR_KR if market == Market.KR else TR_US
        self._field_count = 46 if market == Market.KR else 26  # 종목당 필드 수(근사)
        self.debug_raw = debug_raw       # >0이면 첫 N개 원시 패킷을 출력(필드 정렬용)
        self.should_stop = should_stop   # 콜백: True면 스트림 종료(데몬 자동정지)

    def _tr_key(self, symbol: str) -> str:
        if self.market == Market.KR:
            return symbol
        exch = US_EXCH.get(self.us_exchange, "NAS")
        return f"D{exch}{symbol}"

    def stream(self, symbols) -> Iterator[TickSnapshot]:
        try:
            import websocket  # websocket-client
        except ImportError as e:
            raise ImportError("websocket-client 필요: pip install websocket-client") from e

        approval = get_approval_key(self.app_key, self.app_secret, self.env)
        if not approval:
            return

        url = WS_REAL if self.env == "real" else WS_PAPER
        ws = websocket.create_connection(url)
        try:
            for sym in symbols:
                ws.send(_subscribe_msg(approval, self._tr, self._tr_key(sym)))
            session_open = _session_open(self.market)
            tz = KST if self.market == Market.KR else ET
            parse = parse_kr if self.market == Market.KR else parse_us

            seen = 0
            while True:
                if self.should_stop is not None and self.should_stop():
                    break
                raw = ws.recv()
                if not raw:
                    continue
                # 원시 패킷 캡처(필드 정렬 검증용)
                if self.debug_raw and seen < self.debug_raw:
                    seen += 1
                    print(f"[kis-raw {seen}] {raw[:600]}")
                # 제어 메시지(JSON): 구독응답/PINGPONG
                if raw[0] in "{[":
                    if "PINGPONG" in raw:
                        ws.send(raw)   # 핑퐁 응답
                    continue
                # 실시간 데이터: 0|TR|count|body
                parts = raw.split("|")
                if len(parts) < 4:
                    continue
                tr_id, body = parts[1], parts[3]
                if tr_id != self._tr:
                    continue
                snap = self._parse_body(body, parse, session_open, tz)
                if snap:
                    yield snap
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

    def _parse_body(self, body: str, parse, session_open, tz):
        fields = body.split("^")
        if len(fields) < self._field_count:
            return None
        # 종목코드: KR=fields[0], US=fields[1](SYMB)
        symbol = fields[0] if self.market == Market.KR else fields[1]
        try:
            return parse(fields, symbol, session_open, datetime.now(tz))
        except (IndexError, ValueError):
            return None
