"""
rag/ — RAG (Retrieval-Augmented Generation) 엔진
=================================================
뉴스, 과거 거래, 온체인 데이터를 ChromaDB에 벡터 저장 →
시그널 발생 시 유사 상황 시맨틱 검색 → 컨텍스트 피처 제공.
"""

from rag.vectorstore import VectorStoreManager
from rag.embedder import Embedder
from rag.retriever import TradeContextRetriever
from rag.context_builder import ContextBuilder

__all__ = [
    "VectorStoreManager",
    "Embedder",
    "TradeContextRetriever",
    "ContextBuilder",
]
