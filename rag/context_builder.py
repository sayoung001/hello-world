"""
context_builder.py — RAG 검색 결과 → 컨텍스트 구성
=====================================================
검색된 유사 거래/뉴스/시장상태를 RL state 피처 또는
LLM 프롬프트용 자연어 컨텍스트로 변환.
"""

from __future__ import annotations


class ContextBuilder:
    """RAG 검색 결과를 RL/LLM에 주입 가능한 형태로 구성"""

    def build_for_rl(self, trade_results: list[dict],
                     news_results: list[dict] | None = None) -> dict:
        """
        RL state에 주입할 수치 피처 생성.

        Returns:
            {
                "similar_trade_winrate": float,
                "similar_trade_avg_roe": float,
                "news_sentiment_rag": float,
                "historical_sl_hit_rate": float,
                "context_confidence": float,
            }
        """
        # 유사 거래 통계
        winrate = 0.5
        avg_roe = 0.0
        sl_hit_rate = 0.5
        ctx_conf = 0.0

        if trade_results:
            wins = 0
            total_roe = 0.0
            sl_hits = 0
            similarities = []

            for r in trade_results:
                meta = r.get("metadata", {})
                exit_type = meta.get("exit_type", "")
                roe = float(meta.get("roe", 0))

                if exit_type == "TP":
                    wins += 1
                if exit_type == "SL":
                    sl_hits += 1
                total_roe += roe
                similarities.append(r.get("similarity", 0))

            n = len(trade_results)
            winrate = wins / n if n > 0 else 0.5
            avg_roe = total_roe / n if n > 0 else 0.0
            sl_hit_rate = sl_hits / n if n > 0 else 0.5
            ctx_conf = sum(similarities) / n if n > 0 else 0.0

        # 뉴스 센티먼트 (RAG 기반)
        news_sentiment = 0.0
        if news_results:
            sentiments = []
            for r in news_results:
                meta = r.get("metadata", {})
                score = float(meta.get("sentiment_score", 0))
                sentiments.append(score)
            if sentiments:
                news_sentiment = sum(sentiments) / len(sentiments)

        return {
            "similar_trade_winrate": round(winrate, 4),
            "similar_trade_avg_roe": round(avg_roe, 2),
            "news_sentiment_rag": round(news_sentiment, 4),
            "historical_sl_hit_rate": round(sl_hit_rate, 4),
            "context_confidence": round(ctx_conf, 4),
        }

    def build_for_llm(self, trade_results: list[dict],
                      news_results: list[dict] | None = None,
                      market_results: list[dict] | None = None) -> str:
        """
        리스크 에이전트 LLM에 제공할 자연어 컨텍스트.

        "과거 유사 상황 5건 분석:
         - 3건 TP 도달 (평균 ROE +156%)
         - 1건 SL 히트 (방향 맞았으나 변동성 급등)
         - 1건 타임아웃 (횡보)
         → 유사 상황 승률 60%, SL 주의 필요"
        """
        parts = []

        # 유사 거래 섹션
        if trade_results:
            parts.append(f"[과거 유사 거래 {len(trade_results)}건]")
            tp_count = sl_count = timeout_count = 0
            tp_roes = []
            sl_roes = []

            for i, r in enumerate(trade_results, 1):
                meta = r.get("metadata", {})
                exit_type = meta.get("exit_type", "unknown")
                roe = float(meta.get("roe", 0))
                symbol = meta.get("symbol", "?")
                direction = meta.get("direction", "?")
                sim = r.get("similarity", 0)

                parts.append(
                    f"  {i}. {symbol} {direction} → {exit_type} "
                    f"(ROE {roe:+.1f}%, 유사도 {sim:.2f})"
                )

                if exit_type == "TP":
                    tp_count += 1
                    tp_roes.append(roe)
                elif exit_type == "SL":
                    sl_count += 1
                    sl_roes.append(roe)
                else:
                    timeout_count += 1

            n = len(trade_results)
            summary = []
            if tp_count:
                avg_tp_roe = sum(tp_roes) / tp_count
                summary.append(f"{tp_count}건 TP (평균 ROE {avg_tp_roe:+.1f}%)")
            if sl_count:
                avg_sl_roe = sum(sl_roes) / sl_count
                summary.append(f"{sl_count}건 SL (평균 ROE {avg_sl_roe:+.1f}%)")
            if timeout_count:
                summary.append(f"{timeout_count}건 타임아웃")

            winrate = tp_count / n * 100 if n > 0 else 0
            parts.append(f"  → 승률 {winrate:.0f}% ({', '.join(summary)})")

        # 뉴스 섹션
        if news_results:
            parts.append(f"\n[관련 뉴스 {len(news_results)}건]")
            for i, r in enumerate(news_results, 1):
                doc = r.get("document", "")[:100]
                sim = r.get("similarity", 0)
                parts.append(f"  {i}. {doc} (유사도 {sim:.2f})")

        # 시장 상태 섹션
        if market_results:
            parts.append(f"\n[유사 시장 상태 {len(market_results)}건]")
            for i, r in enumerate(market_results, 1):
                doc = r.get("document", "")[:100]
                sim = r.get("similarity", 0)
                parts.append(f"  {i}. {doc} (유사도 {sim:.2f})")

        return "\n".join(parts) if parts else "컨텍스트 데이터 없음"
