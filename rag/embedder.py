"""
embedder.py — 텍스트 → 임베딩 변환
=====================================
sentence-transformers 기반. all-MiniLM-L6-v2 (경량, CPU OK).
"""

from __future__ import annotations
import numpy as np

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

# 기본 모델 (384차원, CPU에서 빠름)
DEFAULT_MODEL = "all-MiniLM-L6-v2"


class Embedder:
    """
    텍스트 → 벡터 임베딩 변환기.

    - 단일/배치 변환 지원
    - 모델 지연 로딩 (첫 호출 시 다운로드)
    """

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model_name = model_name
        self._model = None

    @property
    def model(self):
        """모델 지연 로딩"""
        if self._model is None:
            if SentenceTransformer is None:
                raise ImportError(
                    "sentence-transformers 필요: pip install sentence-transformers"
                )
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, text: str) -> list[float]:
        """단일 텍스트 → 벡터"""
        vec = self.model.encode(text, convert_to_numpy=True)
        return vec.tolist()

    def embed_batch(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        """배치 텍스트 → 벡터 리스트"""
        if not texts:
            return []
        vecs = self.model.encode(texts, batch_size=batch_size, convert_to_numpy=True)
        return vecs.tolist()

    @property
    def dimension(self) -> int:
        """임베딩 차원 수"""
        return self.model.get_sentence_embedding_dimension()

    def similarity(self, text_a: str, text_b: str) -> float:
        """두 텍스트 간 코사인 유사도"""
        vec_a = np.array(self.embed(text_a))
        vec_b = np.array(self.embed(text_b))
        dot = np.dot(vec_a, vec_b)
        norm = np.linalg.norm(vec_a) * np.linalg.norm(vec_b)
        if norm == 0:
            return 0.0
        return float(dot / norm)
