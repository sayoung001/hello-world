"""
vectorstore.py — ChromaDB 벡터 스토어 관리
============================================
카테고리별 컬렉션 분리 (뉴스, 거래, 시장상태, 온체인).
sentence-transformers로 임베딩 생성.
"""

from __future__ import annotations
import os
import chromadb
from chromadb.config import Settings


# 컬렉션 정의
COLLECTIONS = {
    "news": {
        "description": "뉴스 헤드라인 + 요약",
        "metadata_fields": ["symbol", "source", "timestamp", "sentiment_score"],
    },
    "trades": {
        "description": "과거 거래 상황 요약 (진입 조건 + 결과)",
        "metadata_fields": ["symbol", "direction", "exit_type", "roe", "gap", "timestamp"],
    },
    "market_states": {
        "description": "시장 레짐 스냅샷 (BTC 상태, 변동성 등)",
        "metadata_fields": ["btc_level", "dominant_trend", "timestamp"],
    },
}

# 기본 저장 경로
DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "chromadb")


class VectorStoreManager:
    """
    ChromaDB 컬렉션 관리자.

    - 컬렉션 생성/접근
    - 문서 추가 (임베딩 포함)
    - 시맨틱 검색
    """

    def __init__(self, db_path: str = ""):
        self.db_path = db_path or DEFAULT_DB_PATH
        os.makedirs(self.db_path, exist_ok=True)
        self._client = chromadb.PersistentClient(path=self.db_path)
        self._collections: dict[str, chromadb.Collection] = {}

    def get_collection(self, name: str) -> chromadb.Collection:
        """컬렉션 가져오기 (없으면 생성)"""
        if name not in self._collections:
            self._collections[name] = self._client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collections[name]

    def add_documents(
        self,
        collection_name: str,
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
        ids: list[str],
    ):
        """문서 + 임베딩을 컬렉션에 추가"""
        coll = self.get_collection(collection_name)
        coll.add(
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
            ids=ids,
        )

    def query(
        self,
        collection_name: str,
        query_embedding: list[float],
        n_results: int = 5,
        where: dict | None = None,
    ) -> dict:
        """시맨틱 검색 (코사인 유사도)"""
        coll = self.get_collection(collection_name)
        kwargs = {
            "query_embeddings": [query_embedding],
            "n_results": min(n_results, coll.count() or 1),
        }
        if where:
            kwargs["where"] = where
        if coll.count() == 0:
            return {"documents": [[]], "metadatas": [[]], "distances": [[]]}
        return coll.query(**kwargs)

    def count(self, collection_name: str) -> int:
        """컬렉션 문서 수"""
        return self.get_collection(collection_name).count()

    def delete_collection(self, name: str):
        """컬렉션 삭제"""
        try:
            self._client.delete_collection(name)
            self._collections.pop(name, None)
        except Exception:
            pass

    def list_collections(self) -> list[str]:
        """모든 컬렉션 이름"""
        return [c.name for c in self._client.list_collections()]

    def health_check(self) -> dict:
        """상태 점검"""
        result = {"status": "ok", "collections": {}}
        for name in COLLECTIONS:
            try:
                cnt = self.count(name)
                result["collections"][name] = {"count": cnt, "status": "ok"}
            except Exception as e:
                result["collections"][name] = {"count": 0, "status": f"error: {e}"}
                result["status"] = "degraded"
        return result
