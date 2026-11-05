# PC 운영 화면 — 텔레그램·노션 없이 브라우저로 확인

VM 안에서 작은 웹 서버가 `signals.csv`·`triggers.csv`·배치 기록을 읽어 한 화면으로 보여 줍니다.
외부에 포트를 열지 않고, PC 에서 **SSH 터널**로 봅니다. 외부 API·LLM·노션을 전혀 호출하지
않으므로 배치가 돌고 있어도, 네트워크가 막혀도 화면은 뜹니다.

## 1. VM 에 등록 (1회)

```bash
cd ~/auto_trade && bash deploy/install_ui.sh
```

`stock-ui` 서비스가 `127.0.0.1:8787` 에서 듭니다. 포트를 바꾸려면 `.env` 에 `STOCK_UI_PORT=`.

## 2. PC 에서 열기

터미널(PowerShell) 하나를 띄워 두고:

```powershell
gcloud compute ssh crypto-bot-base-20260228 -- -L 8787:127.0.0.1:8787
```

(gcloud 가 없으면 `ssh -L 8787:127.0.0.1:8787 사용자@VM주소`)

브라우저에서 **http://localhost:8787** 을 엽니다. 터미널을 닫으면 터널도 끊깁니다.

## 3. 화면

| 탭 | 내용 |
|---|---|
| 추천 | 최근 N일 BUY·WATCH(회사명·원화·관련 ETF 포함), 결과(TP/SL/대기), **실행 여부 버튼(샀다/안 샀다)**, 게이트 차단·AVOID 목록, 텔레그램 다이제스트 원문 |
| 진입 트리거 | 대기·발화(🎯)·무효(⛔)·만료, 조건·기준가·도달가·손절·발화 시각·노션 링크 |
| 성과 | 가상계좌 손익·승률·PF, BUY vs WATCH, 보유일별 수익 곡선, 주간 요약 원문, LLM 사용량·비용 |
| 상태·로그 | 시간표(.env 기준 — KR 이 켜졌는지 바로 보임), 마지막 배치 요약(US/KR), 배치 실패 기록, autopull 상태, 데몬 로그 끝 N줄 |

60초마다 자동 갱신. 상단에서 시장(US/KR)·기간 필터.

## 4. 포트를 열어야 한다면 (권장하지 않음)

`.env` 에 `STOCK_UI_TOKEN=긴임의문자열` 을 두고 서비스 파일의 `ExecStart` 에 `--host 0.0.0.0` 을 붙입니다.
그 뒤로는 `http://VM:8787/?token=…` 처럼 토큰이 있어야 열립니다. 토큰 없이 0.0.0.0 으로는 기동을 거부합니다.
GCE 방화벽도 따로 열어야 하며, 가능하면 터널을 쓰세요.

## 5. 문제가 있을 때

```bash
sudo systemctl status stock-ui          # 서비스 상태
tail -20 ~/auto_trade/logs/ui.log       # 서버 로그
curl -s http://127.0.0.1:8787/healthz   # {"ok": true} 이면 서버는 산 것 → 터널 문제
```
