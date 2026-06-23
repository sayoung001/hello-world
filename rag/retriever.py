"""
retriever.py — 시맨틱 검색기
==============================
시그널 발생 시 유사한 과거 상황을 검색하여 컨텍스트 제공.
시간 가중 검색 (최근 데이터에 가중치).
"""

from __future__ import annotations
import math
import time
from datetime import datetime, timezone, timedelta

from rag.vectorstore import VectorStoreManager
from rag.embedder import Embedder

KST = timezone(timedelta(hours=9))


class TradeContextRetriever:
    """
    시그널 조건을 자연어 쿼리로 변환 → 유사 상황 검색.
    시간 가중치로 최근 데이터를 우선.
    """

    def __init__(self, store: VectorStoreManager, embedder: Embedder):
        self.store = store
        self.embedder = embedder

    def _signal_to_query(self, signal: dict) -> str:
        """시그널 조건을 자연어 쿼리 문자열로 변환"""
        parts = []
        if signal.get("symbol"):
            parts.append(signal["symbol"])
        if signal.get("direction"):
            parts.append(f"{'롱' if signal['direction'] == 'long' else '숏'} 브레이크아웃")
        if signal.get("gap"):
            parts.append(f"GAP {signal['gap']:.1f}%")
        if signal.get("confidence"):
            parts.append(f"confidence {signal['confidence']}")
        if signal.get("adx"):
            parts.append(f"ADX {signal['adx']}")
        if signal.get("btc_level") is not None:
            parts.append(f"BTC 필터 레벨 {signal['btc_level']}")
        if signal.get("funding_rate"):
            parts.append(f"펀딩레이트 {signal['funding_rate']:.4f}")
        return "EMA 수렴 후 " + ", ".join(parts) if parts else "EMA 수렴 브레이크아웃"

    def retrieve_similar_trades(
        self, signal: dict, top_k: int = 5
    ) -> list[dict]:
        """
        시그널과 유사한 과거 거래 검색.

        Returns:
            [{"document": str, "metadata": dict, "similarity": float, "recency_weight": float}, ...]
        """
        query_text = self._signal_to_query(signal)
        query_vec = self.embedder.embed(query_text)

        # 심볼 필터 (있으면 적용)
        where = None
        if signal.get("symbol"):
            where = {"symbol": signal["symbol"]}

        results = self.store.query(
            collection_name="trades",
            query_embedding=query_vec,
            n_results=top_k * 2,  # 시간 가중 후 필터링 위해 여유분 검색
            where=where,
        )

        # 결과가 없으면 심볼 필터 없이 재검색
        docs = results.get("documents", [[]])[0]
        if not docs and where:
            results = self.store.query(
                collection_name="trades",
                query_embedding=query_vec,
                n_results=top_k * 2,
            )
            docs = results.get("documents", [[]])[0]

        if not docs:
            return []

        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        # 시간 가중치 적용 + 정렬
        now_ts = time.time()
        weighted = []
        for doc, meta, dist in zip(docs, metadatas, distances):
            # 코사인 거리 → 유사도 (ChromaDB: distance = 1 - similarity)
            similarity = max(0.0, 1.0 - dist)
            # 시간 감쇠 (반감기 90일)
            doc_ts = meta.get("timestamp", 0)
            if isinstance(doc_ts, str):
                try:
                    doc_ts = datetime.fromisoformat(doc_ts).timestamp()
                except Exception:
                    doc_ts = 0
            days_ago = (now_ts - float(doc_ts)) / 86400 if doc_ts else 180
            recency = math.exp(-0.0077 * days_ago)  # 90일 반감기

            final_score = similarity * 0.7 + recency * 0.3
            weighted.append({
                "document": doc,
                "metadata": meta,
                "similarity": round(similarity, 4),
                "recency_weight": round(recency, 4),
                "final_score": round(final_score, 4),
            })

        weighted.sort(key=lambda x: x["final_score"], reverse=True)
        return weighted[:top_k]

    def retrieve_news(
        self, symbol: str, hours_back: int = 24, top_k: int = 5
    ) -> list[dict]:
        """최근 뉴스 중 해당 코인 관련 시맨틱 검색"""
        query_text = f"{symbol} 암호화폐 뉴스 시장 영향"
        query_vec = self.embedder.embed(query_text)

        results = self.store.query(
            collection_name="news",
            query_embedding=query_vec,
            n_results=top_k,
        )

        docs = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        output = []
        for doc, meta, dist in zip(docs, metadatas, distances):
            similarity = max(0.0, 1.0 - dist)
            output.append({
                "document": doc,
                "metadata": meta,
                "similarity": round(similarity, 4),
            })
        return output

    def retrieve_market_state(
        self, current_state: dict, top_k: int = 5
    ) -> list[dict]:
        """현재 시장 상태와 유사한 과거 상태 검색"""
        parts = []
        if current_state.get("btc_level") is not None:
            parts.append(f"BTC 레벨 {current_state['btc_level']}")
        if current_state.get("dominant_trend"):
            parts.append(f"추세 {current_state['dominant_trend']}")
        if current_state.get("volatility"):
            parts.append(f"변동성 {current_state['volatility']}")
        query_text = "시장 상태: " + ", ".join(parts) if parts else "시장 상태 분석"
        query_vec = self.embedder.embed(query_text)

        results = self.store.query(
            collection_name="market_states",
            query_embedding=query_vec,
            n_results=top_k,
        )

        docs = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        output = []
        for doc, meta, dist in zip(docs, metadatas, distances):
            similarity = max(0.0, 1.0 - dist)
            output.append({
                "document": doc,
                "metadata": meta,
                "similarity": round(similarity, 4),
            })
        return output
