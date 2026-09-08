"""
일일 배치 다이제스트 — 추천·섹터·매크로를 텔레그램으로 밀어 보낸다.

왜 필요한가:
  기존 정책은 "배치=Notion, 실시간=Telegram"이었다. 그런데 그 정책에는 구멍이 있다.
    - Notion을 설정하지 않으면 배치 결과가 **콘솔에만** 출력된다.
      SSH VM에서 cron/데몬으로 돌리면 아무도 그 콘솔을 보지 않는다.
    - Notion은 '가서 보는' 매체다. 미국장 배치는 KST 06~07시에 끝나므로
      **먼저 알려주지 않으면 그날 장을 놓친다.**
  → Notion은 아카이브(기록·검색), Telegram은 푸시(오늘 뭘 볼지)로 역할을 나눈다.

설계:
  - 순수 함수로 문자열을 만들고(테스트 가능), 전송은 호출부가 담당한다.
  - **parse_mode 없이 평문**으로 보낸다. LLM이 만든 문장에 `_`, `*`, `[` 가 섞이면
    Markdown 파싱이 깨져 메시지 전체가 실패한다. 구조는 이모지로 표현한다.
  - 텔레그램 1건 상한 4096자 → 청크로 나눠 보낸다(Telegram.send_message가 처리).
"""

from __future__ import annotations

from typing import Any, Optional

MAX_RECO_IN_DIGEST = 8      # 상위 몇 건까지 상세히 보낼지
MAX_SECTOR_LINES = 12


def _num(v, fmt: str = "{:,.2f}") -> str:
    try:
        if v is None or v == "":
            return "-"
        f = float(v)
        return "-" if f != f else fmt.format(f)
    except (TypeError, ValueError):
        return str(v)


def batch_digest(result: Any, sector_lines: Optional[list[str]] = None,
                 max_reco: int = MAX_RECO_IN_DIGEST) -> str:
    """
    BatchResult → 텔레그램 평문 다이제스트.

    result: pipeline.daily_batch.BatchResult
    """
    screen = result.screen
    macro = screen.macro
    as_of = getattr(screen, "as_of", "") or result.date
    date_str = (result.date if as_of == result.date
                else f"{result.date} (기준봉 {as_of})")

    L: list[str] = [
        f"📈 [{result.market.value}] {date_str} 일일 추천",
        "",
        f"🌐 매크로 L{macro.level} ({macro.label})",
        f"   {macro.detail}",
    ]

    # ── 섹터 ──
    lines = list(sector_lines or getattr(result, "sector_lines", []) or [])
    if lines:
        L += ["", "🏭 섹터 현황"]
        for ln in lines[:MAX_SECTOR_LINES]:
            L.append(f"   • {ln}")
        if len(lines) > MAX_SECTOR_LINES:
            L.append(f"   … 외 {len(lines) - MAX_SECTOR_LINES}개")
    else:
        L += ["", "🏭 섹터 현황 — 진단 없음(수집 실패 또는 비활성)"]

    # ── 추천 ──
    recos = list(getattr(result.agents, "recommendations", []) or [])
    cands = screen.candidates
    L += ["", f"⭐ 추천 {len(recos)}건 / 후보 {0 if cands is None else len(cands)}건"]

    if recos:
        rows = [] if cands is None or cands.empty else cands.to_dict("records")
        for i, r in enumerate(recos[:max_reco]):
            row = rows[i] if i < len(rows) else {}
            sym = row.get("symbol") or r.get("ticker") or "-"
            pnl = r.get("expected_pnl_today") or {}
            targets = r.get("targets") or []
            de = row.get("days_to_earnings")
            earn = (f" · 실적 D-{int(de)}" if de is not None and de == de else "")
            L += [
                "",
                f"{i + 1}. {sym}  [{r.get('recommend', '-')}/{r.get('horizon', '-')}] "
                f"확신 {_num(r.get('conviction'), '{:.2f}')}",
                f"   Eff {_num(row.get('effective_score'))} · "
                f"종가 {_num(row.get('close'))} · {row.get('strategies', '-')}{earn}",
                f"   진입: {str(r.get('entry_rule', '-'))[:120]}",
                f"   손절 {_num(r.get('stop'))} · "
                f"목표 {', '.join(_num(t) for t in targets) or '-'} · "
                f"R:R {_num(row.get('rr_ratio'), '{:.2f}')}",
                f"   당일 예상 P10 {_num(pnl.get('p10_pct'), '{:+.1f}')}% / "
                f"P50 {_num(pnl.get('p50_pct'), '{:+.1f}')}% / "
                f"P90 {_num(pnl.get('p90_pct'), '{:+.1f}')}%",
            ]
        if len(recos) > max_reco:
            L.append(f"\n… 외 {len(recos) - max_reco}건 (전체는 Notion 참조)")
    elif cands is not None and not cands.empty:
        # LLM 없이 규칙 스캔만 돈 경우
        L.append("(LLM 미실행 — 규칙 스캔 결과)")
        for r in cands.head(max_reco).itertuples():
            L.append(f"   • {r.symbol} Eff {getattr(r, 'effective_score', '-')} · "
                     f"{getattr(r, 'strategies', '-')}")
    else:
        L.append("   매수 후보 없음 — 게이트 차단이거나 임계 미달입니다.")

    # ── 게이트 차단 ──
    gated = getattr(screen, "gated", None)
    if gated is not None and not gated.empty:
        L += ["", f"🚧 게이트 차단 {len(gated)}건 (기록됨)"]
        for r in gated.head(5).itertuples():
            L.append(f"   • [{r.gated_by}] {r.symbol} Eff {r.effective_score}")
        if len(gated) > 5:
            L.append(f"   … 외 {len(gated) - 5}건")

    # ── 매도 신호 ──
    exits = screen.exits
    if exits is not None and not exits.empty:
        L += ["", f"🔻 매도 신호 {len(exits)}건 — 보유 중이면 청산 점검"]
        for r in exits.head(8).itertuples():
            L.append(f"   • {r.symbol} Exit {r.exit_score}/5 · 종가 {_num(r.close)}")
        if len(exits) > 8:
            L.append(f"   … 외 {len(exits) - 8}건")

    # ── 운영 ──
    n_fail = len(getattr(screen, "failures", {}) or {})
    L += ["", f"ℹ️ 채점 {0 if screen.scored_all is None else len(screen.scored_all)}종목 "
          f"· 실패 {n_fail}건"]
    L += ["", "※ 분석 보조이며 투자 권유가 아닙니다. 실행 여부는 본인 판단입니다.",
          "   실행 입력: python -m stock_auto.pipeline.triage"]
    return "\n".join(L)
