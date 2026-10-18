"""
성과 대시보드 — signals.csv + forward_returns.csv → 자급자족 HTML 한 장.

외부 스크립트·CDN 없이 인라인 SVG로 그려서 폰에서도, 오프라인에서도 열린다.
  구성: KPI 타일 · 에쿼티 곡선 · 보유일별 수익 곡선 · 추천 목록(결과·D+N 수익률)

사용:
  .venv/bin/python -m stock_auto.tracking.dashboard                 # logs/dashboard.html 생성
  .venv/bin/python -m stock_auto.tracking.dashboard --send          # 텔레그램 파일로도 전송
  .venv/bin/python -m stock_auto.tracking.dashboard --out /path.html
"""

from __future__ import annotations

import argparse
import html
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from stock_auto.tracking import forward, pnl

DEFAULT_OUT = "logs/dashboard.html"

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--tx:#1b1f24;--mut:#6b7280;--line:#e5e7eb;
--up:#1a9c7b;--dn:#d9534f;--acc:#3b6fd6}
@media(prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#171b21;--tx:#e6e8eb;
--mut:#9aa3ad;--line:#2a3039;--up:#3ccf9e;--dn:#f0716c;--acc:#7aa2ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);
font:14px/1.5 -apple-system,"Segoe UI","Noto Sans KR",sans-serif;padding:16px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:22px 0 8px;color:var(--mut)}
.sub{color:var(--mut);font-size:12px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:12px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.tile .k{font-size:11px;color:var(--mut)}.tile .v{font-size:22px;font-weight:600;margin-top:2px}
.tile .s{font-size:11px;color:var(--mut)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;overflow-x:auto}
svg text{fill:var(--mut);font-size:10px}
table{border-collapse:collapse;width:100%;font-size:12.5px}th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{color:var(--mut);font-weight:500}td:first-child,th:first-child,td:nth-child(2),th:nth-child(2){text-align:left}
.up{color:var(--up)}.dn{color:var(--dn)}.mut{color:var(--mut)}
.pill{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11px;border:1px solid var(--line)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:760px){.grid2{grid-template-columns:1fr}}
"""


def _f(v) -> Optional[float]:
    try:
        if v in (None, ""):
            return None
        x = float(v)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def _cls(v: Optional[float]) -> str:
    return "" if v is None else ("up" if v > 0 else "dn" if v < 0 else "")


def _pct(v: Optional[float], nd: int = 2) -> str:
    return "–" if v is None else f'<span class="{_cls(v)}">{v:+.{nd}f}%</span>'


def _usd(v: Optional[float]) -> str:
    return "–" if v is None else f'<span class="{_cls(v)}">${v:+,.0f}</span>'


# ── SVG 도우미 ──────────────────────────────────────────────────────────────
def _line_svg(points: list[tuple[str, float]], w: int = 640, h: int = 220,
              zero_line: bool = True, fmt: str = "${:+,.0f}") -> str:
    if len(points) < 2:
        return '<div class="mut">표본 부족 (2건 이상 청산 후 표시)</div>'
    pad_l, pad_r, pad_t, pad_b = 52, 12, 12, 26
    ys = [p[1] for p in points]
    lo, hi = min(min(ys), 0 if zero_line else min(ys)), max(max(ys), 0 if zero_line else max(ys))
    if hi == lo:
        hi = lo + 1
    n = len(points)
    def X(i): return pad_l + (w - pad_l - pad_r) * i / (n - 1)
    def Y(v): return pad_t + (h - pad_t - pad_b) * (hi - v) / (hi - lo)
    path = " ".join(f"{'M' if i == 0 else 'L'}{X(i):.1f},{Y(v):.1f}" for i, (_, v) in enumerate(points))
    area = path + f" L{X(n - 1):.1f},{Y(0):.1f} L{X(0):.1f},{Y(0):.1f} Z"
    last = points[-1][1]
    color = "var(--up)" if last >= 0 else "var(--dn)"
    ticks = [lo, (lo + hi) / 2, hi]
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" preserveAspectRatio="none" style="max-height:{h}px">']
    for t in ticks:
        out.append(f'<line x1="{pad_l}" y1="{Y(t):.1f}" x2="{w - pad_r}" y2="{Y(t):.1f}" stroke="var(--line)" stroke-width="1"/>')
        out.append(f'<text x="{pad_l - 6}" y="{Y(t) + 3:.1f}" text-anchor="end">{fmt.format(t)}</text>')
    if zero_line:
        out.append(f'<line x1="{pad_l}" y1="{Y(0):.1f}" x2="{w - pad_r}" y2="{Y(0):.1f}" stroke="var(--mut)" stroke-dasharray="3 3"/>')
    out.append(f'<path d="{area}" fill="{color}" opacity="0.12"/>')
    out.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2"/>')
    step = max(1, n // 6)
    for i in range(0, n, step):
        out.append(f'<text x="{X(i):.1f}" y="{h - 8}" text-anchor="middle">{html.escape(str(points[i][0])[5:])}</text>')
    out.append(f'<circle cx="{X(n - 1):.1f}" cy="{Y(last):.1f}" r="3.5" fill="{color}"/>')
    out.append("</svg>")
    return "".join(out)


def _bar_svg(cv: list[dict], w: int = 640, h: int = 220) -> str:
    got = [c for c in cv if c.get("n")]
    if not got:
        return '<div class="mut">아직 D+1 이후 데이터가 없습니다</div>'
    pad_l, pad_r, pad_t, pad_b = 46, 12, 14, 26
    vals = [c["avg"] for c in got]
    lo, hi = min(min(vals), 0), max(max(vals), 0)
    if hi == lo:
        hi = lo + 1
    def Y(v): return pad_t + (h - pad_t - pad_b) * (hi - v) / (hi - lo)
    bw = (w - pad_l - pad_r) / len(got)
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" preserveAspectRatio="none" style="max-height:{h}px">']
    for t in (lo, 0, hi):
        out.append(f'<line x1="{pad_l}" y1="{Y(t):.1f}" x2="{w - pad_r}" y2="{Y(t):.1f}" stroke="var(--line)"/>')
        out.append(f'<text x="{pad_l - 6}" y="{Y(t) + 3:.1f}" text-anchor="end">{t:+.1f}%</text>')
    best = forward.best_horizon(got)
    for i, c in enumerate(got):
        x = pad_l + bw * i + bw * 0.2
        y0, y1 = Y(0), Y(c["avg"])
        col = "var(--up)" if c["avg"] >= 0 else "var(--dn)"
        op = "1" if best and best["day"] == c["day"] else "0.55"
        out.append(f'<rect x="{x:.1f}" y="{min(y0, y1):.1f}" width="{bw * 0.6:.1f}" '
                   f'height="{abs(y0 - y1):.1f}" fill="{col}" opacity="{op}" rx="2"/>')
        out.append(f'<text x="{x + bw * 0.3:.1f}" y="{h - 8}" text-anchor="middle">D+{c["day"]}</text>')
        out.append(f'<text x="{x + bw * 0.3:.1f}" y="{min(y0, y1) - 4:.1f}" text-anchor="middle">'
                   f'{c["win_rate"]:.0f}%·n{c["n"]}</text>')
    out.append("</svg>")
    return "".join(out)


# ── 본문 ───────────────────────────────────────────────────────────────────
def build_html(rows: list[dict], fwd_rows: list[dict]) -> str:
    fwd_by = {r["signal_id"]: r for r in fwd_rows}
    trades = pnl.closed_trades(rows)
    s = pnl.summarize(trades)
    se = pnl.summarize(pnl.closed_trades(rows, executed_only=True))
    recos = [r for r in rows if r.get("source") == "batch" and not r.get("gated_by")]
    waiting = sum(1 for r in recos if not r.get("exit_type"))
    cv = forward.curve(fwd_rows)
    best = forward.best_horizon(cv)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    tiles = [
        ("추천 누적", f"{len(recos)}건", f"청산 {s['n']} · 대기 {waiting}"),
        ("가상계좌 손익", _usd(s.get("total_pnl")) if s["n"] else "–",
         f"${pnl.position_usd():,.0f}/종목 · 최대낙폭 ${s.get('max_dd', 0):,.0f}" if s["n"] else "청산 대기"),
        ("승률", f"{s['win_rate']:.0f}%" if s["n"] else "–",
         f"TP {s.get('tp', 0)} / SL {s.get('sl', 0)} / TO {s.get('to', 0)}" if s["n"] else ""),
        ("손익비 PF", pnl._pf(s["pf"]) if s["n"] else "–",
         f"익절 ${s['avg_win']:+,.0f} / 손절 ${s['avg_loss']:+,.0f}" if s["n"] else ""),
        ("평균 보유일", f"{s['avg_days']:.1f}일" if s["n"] else "–",
         f"최적 D+{best['day']} ({best['avg']:+.2f}%)" if best else "표본 부족"),
        ("실제 실행분", _usd(se.get("total_pnl")) if se["n"] else "–",
         f"{se['n']}건 · 승률 {se['win_rate']:.0f}%" if se["n"] else "triage 미입력"),
    ]
    tile_html = "".join(f'<div class="tile"><div class="k">{k}</div><div class="v">{v}</div>'
                        f'<div class="s">{sub}</div></div>' for k, v, sub in tiles)

    eq_pts = [(c["date"], c["equity"]) for c in s.get("curve", [])]
    eq_svg = _line_svg([("시작", 0.0)] + eq_pts) if eq_pts else _line_svg([])

    # 추천 표 (최근 60건)
    hcols = "".join(f"<th>D+{h}</th>" for h in forward.HORIZONS)
    trs = []
    for r in sorted(recos, key=lambda x: x.get("date", ""), reverse=True)[:60]:
        fw = fwd_by.get(r["signal_id"], {})
        et = r.get("exit_type") or ""
        pill = {"tp": '<span class="pill up">TP</span>', "sl": '<span class="pill dn">SL</span>',
                "timeout": '<span class="pill">TO</span>', "skip": '<span class="pill mut">미체결</span>'
                }.get(et, '<span class="pill mut">대기</span>')
        ex = r.get("executed") or ""
        exs = {"yes": "✅", "no": "✖", "skip": "–"}.get(ex, "?")
        fwd_cells = "".join(f"<td>{_pct(_f(fw.get(f'r{h}')), 1)}</td>" for h in forward.HORIZONS)
        trs.append(
            f"<tr><td>{html.escape(str(r.get('date', '')))}</td>"
            f"<td><b>{html.escape(str(r.get('symbol', '')))}</b> "
            f"<span class='mut'>{html.escape(str(r.get('recommend', '')))}/"
            f"{html.escape(str(r.get('horizon', '')))}</span></td>"
            f"<td>{_f(r.get('conviction')) or 0:.2f}</td>"
            f"<td>{pill}</td><td>{_pct(_f(r.get('ret_pct')))}</td>"
            f"<td>{r.get('days_held') or '–'}</td><td>{exs}</td>{fwd_cells}"
            f"<td>{_pct(_f(fw.get('mfe20')), 1)}</td><td>{_pct(_f(fw.get('mae20')), 1)}</td></tr>")
    table = (f"<table><thead><tr><th>날짜</th><th>종목</th><th>확신</th><th>결과</th>"
             f"<th>수익</th><th>보유</th><th>실행</th>{hcols}<th>MFE20</th><th>MAE20</th></tr></thead>"
             f"<tbody>{''.join(trs) or '<tr><td colspan=16 class=mut>기록 없음</td></tr>'}</tbody></table>")

    monthly = s.get("monthly") or {}
    mrows = "".join(f"<tr><td>{m}</td><td>{_usd(v)}</td></tr>" for m, v in monthly.items())

    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>추천 성과 대시보드</title>
<style>{CSS}</style></head><body>
<h1>📊 추천 성과 대시보드</h1>
<div class="sub">갱신 {now} · 진입 = 다음 거래일 시가 · 청산 = 목표/손절/보유상한 중 먼저 · 종목당 ${pnl.position_usd():,.0f}</div>
<div class="tiles">{tile_html}</div>
<div class="grid2">
<div><h2>에쿼티 곡선 (청산일 기준 누적 손익)</h2><div class="card">{eq_svg}</div></div>
<div><h2>보유일별 평균 수익 (배리어 무관 · 막대 위 = 승률·표본)</h2><div class="card">{_bar_svg(cv)}</div></div>
</div>
<h2>월별 손익</h2><div class="card"><table><thead><tr><th>월</th><th>손익</th></tr></thead>
<tbody>{mrows or '<tr><td colspan=2 class=mut>청산 완료 거래 없음</td></tr>'}</tbody></table></div>
<h2>추천 목록 (최근 60건 · D+N = 그냥 들고 있었을 때 종가 수익률)</h2><div class="card">{table}</div>
<p class="sub">※ 분석 보조이며 투자 권유가 아닙니다. 실행 여부 입력: <code>.venv/bin/python -m stock_auto.pipeline.triage</code></p>
</body></html>"""


def write(out: Optional[str] = None, rows: Optional[list[dict]] = None,
          fwd_rows: Optional[list[dict]] = None) -> Path:
    from stock_auto.tracking import store
    rows = store.load() if rows is None else rows
    fwd_rows = forward.load() if fwd_rows is None else fwd_rows
    p = Path(out or DEFAULT_OUT)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(build_html(rows, fwd_rows), encoding="utf-8")
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description="성과 대시보드 HTML")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--send", action="store_true", help="텔레그램 파일 전송")
    args = ap.parse_args()
    from stock_auto.config.env import load_env, get_secrets
    load_env()
    p = write(args.out)
    print(f"[dashboard] 저장 → {p}")
    if args.send:
        from stock_auto.notify.telegram import Telegram
        sec = get_secrets()
        tg = Telegram(sec.telegram_bot_token, sec.telegram_chat_id)
        ok = tg.send_document(str(p), caption=f"추천 성과 대시보드 {datetime.now():%Y-%m-%d}")
        print(f"[dashboard] 텔레그램 {'전송' if ok else '실패'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
