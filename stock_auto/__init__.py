"""stock_auto — 반자동 주식 추천·알림 시스템(추천과 알림만, 주문 실행 없음).

패키지를 import 하는 순간 실행 폴더의 .env 를 환경변수로 올린다.

왜 여기서 하나:
  .env 는 원래 get_secrets() 가 처음 불릴 때 로드됐다. 그런데 ENABLE_KR,
  STOCK_BATCH_TIME_KST, KIS_MAX_SUBSCRIPTIONS, STOCK_KR_UNIVERSE_N 처럼 os.environ 을
  **직접** 읽는 설정이 그보다 먼저 평가됐다 — 데몬 main() 은 시장 목록을 먼저 정하고
  나서야 시크릿을 읽었고, 몇몇 상수는 모듈 import 시점에 읽는다. 결과: .env 에
  ENABLE_KR=1 을 적어도 데몬은 미국장만 켰고, 국내장 배치는 영영 돌지 않았다.
  systemd 유닛에 EnvironmentFile 이 없으니 .env 가 유일한 경로다.

load_env 는 .env 가 없으면 아무 일도 하지 않고, 이미 있는 OS 환경변수는 덮어쓰지 않는다.
"""

try:
    from stock_auto.config.env import load_env as _load_env
    _load_env()
except Exception:  # noqa: BLE001 — 환경 로딩 실패가 import 를 막으면 안 된다
    pass
