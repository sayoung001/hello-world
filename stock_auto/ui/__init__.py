"""PC 운영 화면 — 텔레그램·노션 없이 브라우저에서 추천·트리거·성과·상태를 본다.

구성:
  ui/status.py  배치·실패 기록(logs/ui/*.json, 다이제스트 원문 txt) — 배치가 쓴다
  ui/data.py    signals.csv·triggers.csv·usage.csv·로그·상태 → 한 번에 JSON 스냅샷
  ui/page.py    HTML/CSS/JS 한 장(외부 CDN 없음, 다크모드, 60초 자동 갱신)
  ui/serve.py   표준 라이브러리 HTTP 서버. 기본 127.0.0.1:8787 — PC 에서는 SSH 터널로 연다

보는 법(PC):
  gcloud compute ssh <VM> -- -L 8787:127.0.0.1:8787     (또는 ssh -L 8787:127.0.0.1:8787 user@vm)
  브라우저  http://localhost:8787

설치(VM, 1회):  bash deploy/install_ui.sh
"""
