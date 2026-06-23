"""
cache.py — Vision AI 결과 캐싱
=================================
동일 심볼+시점 5분 내 중복 호출 방지.
차트 이미지와 분석 결과를 메모리 캐시.
"""

from __future__ import annotations
import time
from collections import OrderedDict


class VisionCache:
    """
    Vision AI 분석 결과 메모리 캐시.

    - 키: (symbol, candle_time) — 15분봉 기준 시점
    - TTL: 5분 (같은 캔들에서 반복 호출 방지)
    - 최대 크기: 100건 (LRU 정리)
    """

    def __init__(self, ttl: int = 300, max_size: int = 100):
        self.ttl = ttl
        self.max_size = max_size
        self._cache: OrderedDict[str, dict] = OrderedDict()

    def _make_key(self, symbol: str, candle_time: int) -> str:
        """캐시 키 생성 (15분봉 단위로 반올림)"""
        # 15분 = 900초 단위로 반올림
        rounded = (candle_time // 900000) * 900000 if candle_time > 1e12 else (candle_time // 900) * 900
        return f"{symbol}_{rounded}"

    def get(self, symbol: str, candle_time: int) -> dict | None:
        """캐시 조회. 없거나 만료되면 None."""
        key = self._make_key(symbol, candle_time)
        entry = self._cache.get(key)
        if entry is None:
            return None
        if time.time() - entry["stored_at"] > self.ttl:
            self._cache.pop(key, None)
            return None
        # LRU: 최근 사용으로 이동
        self._cache.move_to_end(key)
        return entry["data"]

    def put(self, symbol: str, candle_time: int, data: dict):
        """캐시 저장"""
        key = self._make_key(symbol, candle_time)
        self._cache[key] = {
            "data": data,
            "stored_at": time.time(),
        }
        self._cache.move_to_end(key)
        # LRU 정리
        while len(self._cache) > self.max_size:
            self._cache.popitem(last=False)

    def clear(self):
        """캐시 전체 초기화"""
        self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)

    def stats(self) -> dict:
        """캐시 통계"""
        now = time.time()
        valid = sum(1 for e in self._cache.values()
                    if now - e["stored_at"] <= self.ttl)
        return {
            "total_entries": len(self._cache),
            "valid_entries": valid,
            "expired_entries": len(self._cache) - valid,
        }
