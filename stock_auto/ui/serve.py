"""
PC 운영 화면 서버 — 표준 라이브러리 http.server. 기본 127.0.0.1:8787.

  .venv/bin/python -m stock_auto.ui.serve                 # 127.0.0.1:8787
  .venv/bin/python -m stock_auto.ui.serve --port 9000
  .venv/bin/python -m stock_auto.ui.serve --host 0.0.0.0  # 외부 노출 — STOCK_UI_TOKEN 필수

PC 에서 보는 법: SSH 터널 → http://localhost:8787
  gcloud compute ssh <VM> -- -L 8787:127.0.0.1:8787

경로:
  GET  /                    화면(HTML)
  GET  /api/snapshot?days=  추천·트리거·성과·상태 JSON
  GET  /api/log?n=&name=    logs/<name> 끝 n줄 (기본 daemon.log)
  GET  /api/digest?name=    다이제스트 원문
  POST /api/triage          {"signal_id","executed":"yes|no|skip","note"}  — 실행 여부 입력

보안: 기본은 루프백만 듣는다. 0.0.0.0 으로 열 때는 STOCK_UI_TOKEN 이 없으면 기동을 거부한다.
토큰이 설정되면 모든 요청에 ?token= 또는 X-Token 헤더가 필요하다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from stock_auto.ui import data as D, status as ST
from stock_auto.ui.page import PAGE_HTML

DEFAULT_PORT = int(os.environ.get("STOCK_UI_PORT", "8787") or 8787)


def _token() -> str:
    return os.environ.get("STOCK_UI_TOKEN", "").strip()


class Handler(BaseHTTPRequestHandler):
    server_version = "stock-ui/1"

    # ── 공통 ──
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _authed(self, q: dict) -> bool:
        tok = _token()
        if not tok:
            return True
        given = (q.get("token", [""])[0] or self.headers.get("X-Token", "")).strip()
        return given == tok

    def log_message(self, fmt, *args):  # noqa: D401 — 접근 로그는 짧게
        sys.stdout.write("[ui] " + (fmt % args) + "\n")

    # ── GET ──
    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._authed(q):
            return self._json({"error": "token"}, 401)
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, PAGE_HTML.encode("utf-8"), "text/html; charset=utf-8")
            if u.path == "/api/snapshot":
                days = int(q.get("days", ["10"])[0] or 10)
                return self._json(D.snapshot(max(1, min(days, 90))))
            if u.path == "/api/log":
                n = int(q.get("n", ["200"])[0] or 200)
                name = q.get("name", ["daemon.log"])[0]
                return self._send(200, D.log_tail(max(10, min(n, 2000)), name).encode("utf-8"),
                                  "text/plain; charset=utf-8")
            if u.path == "/api/digest":
                name = q.get("name", [""])[0]
                return self._send(200, (ST.read_digest(name) or "(없음)").encode("utf-8"),
                                  "text/plain; charset=utf-8")
            if u.path == "/healthz":
                return self._json({"ok": True})
            return self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001 — 한 요청 실패가 서버를 죽이지 않게
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    # ── POST ──
    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._authed(q):
            return self._json({"error": "token"}, 401)
        try:
            n = int(self.headers.get("Content-Length", "0") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
        except (ValueError, json.JSONDecodeError):
            return self._json({"error": "bad json"}, 400)
        try:
            if u.path == "/api/triage":
                from stock_auto.tracking import store
                sid = str(body.get("signal_id", "")).strip()
                ex = str(body.get("executed", "")).strip().lower()
                note = str(body.get("note", "")).strip()[:120]
                if not sid or ex not in store.EXECUTED_VALUES:
                    return self._json({"error": f"executed 는 {store.EXECUTED_VALUES} 중 하나"}, 400)
                ok = store.set_executed(sid, ex, note or "UI 입력")
                return self._json({"ok": bool(ok)}, 200 if ok else 404)
            return self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)


def make_server(host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PC 운영 화면 서버")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    a = ap.parse_args(argv)
    if a.host not in ("127.0.0.1", "localhost", "::1") and not _token():
        print("❌ 루프백이 아닌 주소로 열려면 STOCK_UI_TOKEN 이 필요합니다(.env). "
              "권장: 기본 127.0.0.1 로 두고 SSH 터널로 접근.")
        return 1
    srv = make_server(a.host, a.port)
    print(f"[ui] http://{a.host}:{a.port}  (토큰 {'사용' if _token() else '없음 — 루프백 전용'})")
    print("[ui] PC 에서: gcloud compute ssh <VM> -- -L {p}:127.0.0.1:{p} → http://localhost:{p}".format(p=a.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
