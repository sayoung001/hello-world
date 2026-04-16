"""
trade_ingester.py — 과거 거래 데이터 → ChromaDB 벡터 저장
==========================================================
signals_all_labeled.csv (370만 건)을 벡터 변환하여 일괄 적재.
실전 거래 결과도 실시간 적재 지원.
"""

from __future__ import annotations
import hashlib
import os

from rag.vectorstore import VectorStoreManager
from rag.embedder import Embedder


class TradeIngester:
    """
    과거 거래 시그널 → 임베딩 → ChromaDB 저장.

    - 초기: labeled_signals CSV 일괄 적재
    - 이후: 실전 거래 결과 실시간 적재
    """

    def __init__(self, store: VectorStoreManager, embedder: Embedder):
        self.store = store
        self.embedder = embedder

    def ingest_from_csv(self, csv_path: str, max_rows: int = 0,
                        batch_size: int = 500) -> int:
        """
        labeled_signals CSV를 벡터 변환하여 적재.

        Args:
            csv_path: signals_all_labeled.csv 경로
            max_rows: 최대 로드 행 수 (0 = 전체)
            batch_size: 배치 크기

        Returns:
            적재된 문서 수
        """
        try:
            import pandas as pd
        except ImportError:
            print("[RAG/거래] pandas 필요: pip install pandas")
            return 0

        if not os.path.exists(csv_path):
            print(f"[RAG/거래] 파일 없음: {csv_path}")
            return 0

        # CSV 로드 (필요 컬럼만)
        usecols = [
            "symbol", "direction", "confidence", "gap", "adx",
            "exit_type", "realized_roe", "hold_candles",
            "mae_pct", "mfe_pct", "direction_correct",
        ]
        try:
            df = pd.read_csv(csv_path, usecols=usecols, nrows=max_rows or None)
        except Exception as e:
            print(f"[RAG/거래] CSV 로드 실패: {e}")
            # usecols가 맞지 않으면 전체 로드 시도
            try:
                df = pd.read_csv(csv_path, nrows=max_rows or None)
            except Exception as e2:
                print(f"[RAG/거래] CSV 전체 로드도 실패: {e2}")
                return 0

        total_ingested = 0

        for start in range(0, len(df), batch_size):
            batch_df = df.iloc[start:start + batch_size]
            texts = []
            metadatas = []
            ids = []

            for _, row in batch_df.iterrows():
                text = self._row_to_text(row)
                meta = self._row_to_metadata(row)
                aid = hashlib.md5(
                    f"trade_{row.get('symbol', '')}_{start}_{_}".encode()
                ).hexdigest()

                texts.append(text)
                metadatas.append(meta)
                ids.append(aid)

            if not texts:
                continue

            embeddings = self.embedder.embed_batch(texts)

            self.store.add_documents(
                collection_name="trades",
                documents=texts,
                embeddings=embeddings,
                metadatas=metadatas,
                ids=ids,
            )
            total_ingested += len(texts)

            if total_ingested % 5000 == 0:
                print(f"  [RAG/거래] 적재 진행: {total_ingested}/{len(df)}")

        print(f"  [RAG/거래] 적재 완료: {total_ingested}건")
        return total_ingested

    def ingest_trade_result(self, trade: dict) -> bool:
        """
        실전 거래 결과 1건 실시간 적재.

        Args:
            trade: {symbol, direction, entry_price, exit_price, exit_type,
                    roe, gap, confidence, adx, hold_candles, timestamp, ...}
        """
        text = self._trade_to_text(trade)
        meta = {
            "symbol": trade.get("symbol", "?"),
            "direction": trade.get("direction", "?"),
            "exit_type": trade.get("exit_type", "unknown"),
            "roe": float(trade.get("roe", 0)),
            "gap": float(trade.get("gap", 0)),
            "timestamp": str(trade.get("timestamp", "")),
        }
        aid = hashlib.md5(
            f"live_{trade.get('symbol', '')}_{trade.get('timestamp', '')}".encode()
        ).hexdigest()

        embedding = self.embedder.embed(text)

        try:
            self.store.add_documents(
                collection_name="trades",
                documents=[text],
                embeddings=[embedding],
                metadatas=[meta],
                ids=[aid],
            )
            return True
        except Exception as e:
            print(f"[RAG/거래] 실시간 적재 실패: {e}")
            return False

    def _row_to_text(self, row) -> str:
        """CSV 행 → 자연어 요약"""
        symbol = row.get("symbol", "?")
        direction = "롱" if row.get("direction") == "long" else "숏"
        conf = row.get("confidence", 0)
        gap = row.get("gap", 0)
        adx = row.get("adx", 0)
        exit_type = row.get("exit_type", "?")
        roe = row.get("realized_roe", 0)
        hold = row.get("hold_candles", 0)
        correct = row.get("direction_correct", False)

        return (
            f"{symbol} EMA 수렴 후 {direction} 브레이크아웃, "
            f"GAP {gap:.1f}%, confidence {conf}, ADX {adx:.0f}. "
            f"결과: {exit_type}, ROE {roe:+.1f}%, {hold}캔들 보유. "
            f"방향 {'정확' if correct else '오류'}."
        )

    def _trade_to_text(self, trade: dict) -> str:
        """실전 거래 결과 → 자연어 요약"""
        symbol = trade.get("symbol", "?")
        direction = "롱" if trade.get("direction") == "long" else "숏"
        exit_type = trade.get("exit_type", "?")
        roe = trade.get("roe", 0)
        gap = trade.get("gap", 0)
        conf = trade.get("confidence", 0)

        return (
            f"{symbol} 실전 {direction} 진입, "
            f"GAP {gap:.1f}%, confidence {conf}. "
            f"결과: {exit_type}, ROE {roe:+.1f}%."
        )

    def _row_to_metadata(self, row) -> dict:
        """CSV 행 → 메타데이터"""
        return {
            "symbol": str(row.get("symbol", "?")),
            "direction": str(row.get("direction", "?")),
            "exit_type": str(row.get("exit_type", "unknown")),
            "roe": float(row.get("realized_roe", 0)),
            "gap": float(row.get("gap", 0)),
            "timestamp": "",
        }

    @property
    def stored_count(self) -> int:
        """저장된 거래 문서 수"""
        return self.store.count("trades")
