"""
rule_store.py — 트레이딩 규칙 저장소
=======================================
ChromaDB 벡터 스토어 기반 규칙 CRUD.
규칙을 시맨틱 벡터로 저장하여 유사 상황에서 관련 규칙을 검색.
"""

from __future__ import annotations
import json
import os
import time
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional

KST = timezone(timedelta(hours=9))

# 규칙 저장 경로 (JSON 파일 백업)
DEFAULT_RULES_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "memory", "rules.json"
)


class TradingRule:
    """단일 트레이딩 규칙"""

    def __init__(self, rule_id: str, condition: str, action: str,
                 confidence: float = 0.5, source: str = "reflection",
                 created_at: float = 0, hits: int = 0, misses: int = 0,
                 weight: float = 1.0, active: bool = True,
                 metadata: dict | None = None):
        self.rule_id = rule_id
        self.condition = condition      # "ETH 수렴 후 롱 시 GAP 0.3~0.5"
        self.action = action            # "SL을 ATR×2.5로 확대"
        self.confidence = confidence    # 초기 신뢰도
        self.source = source            # "reflection" | "bootstrap" | "manual"
        self.created_at = created_at or time.time()
        self.hits = hits                # 적중 횟수
        self.misses = misses            # 실패 횟수
        self.weight = weight            # 현재 가중치 (감쇠 적용)
        self.active = active
        self.metadata = metadata or {}

    @property
    def hit_rate(self) -> float:
        """적중률"""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.5

    @property
    def sample_size(self) -> int:
        return self.hits + self.misses

    @property
    def effective_confidence(self) -> float:
        """가중치 반영 실효 신뢰도"""
        return self.confidence * self.weight

    @property
    def age_days(self) -> float:
        """생성 이후 경과 일수"""
        return (time.time() - self.created_at) / 86400

    def to_text(self) -> str:
        """규칙을 자연어 문자열로 변환 (임베딩용)"""
        return f"조건: {self.condition}. 행동: {self.action}"

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "condition": self.condition,
            "action": self.action,
            "confidence": self.confidence,
            "source": self.source,
            "created_at": self.created_at,
            "hits": self.hits,
            "misses": self.misses,
            "weight": self.weight,
            "active": self.active,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TradingRule":
        return cls(**d)


class RuleStore:
    """
    트레이딩 규칙 저장소.

    이중 저장:
    1. 메모리 dict (빠른 접근)
    2. JSON 파일 (영속성)

    ChromaDB 연동은 선택적 (임베딩 기반 유사 규칙 검색용).
    """

    def __init__(self, rules_path: str = "", embedder=None, vectorstore=None):
        self.rules_path = rules_path or DEFAULT_RULES_PATH
        self._rules: dict[str, TradingRule] = {}
        self._embedder = embedder       # rag.embedder.Embedder (선택)
        self._vectorstore = vectorstore  # rag.vectorstore.VectorStoreManager (선택)
        self._load()

    def _load(self):
        """파일에서 규칙 로드"""
        if not os.path.exists(self.rules_path):
            return
        try:
            with open(self.rules_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for d in data:
                rule = TradingRule.from_dict(d)
                self._rules[rule.rule_id] = rule
        except Exception as e:
            print(f"[RuleStore] 규칙 로드 실패: {e}")

    def _save(self):
        """파일로 규칙 저장"""
        os.makedirs(os.path.dirname(self.rules_path) or ".", exist_ok=True)
        data = [r.to_dict() for r in self._rules.values()]
        with open(self.rules_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def add(self, condition: str, action: str, confidence: float = 0.5,
            source: str = "reflection", metadata: dict | None = None) -> TradingRule:
        """새 규칙 추가"""
        rule_id = hashlib.md5(
            f"{condition}_{action}_{time.time()}".encode()
        ).hexdigest()[:12]

        rule = TradingRule(
            rule_id=rule_id,
            condition=condition,
            action=action,
            confidence=confidence,
            source=source,
            metadata=metadata,
        )
        self._rules[rule_id] = rule
        self._save()

        # ChromaDB 벡터 추가 (선택)
        if self._embedder and self._vectorstore:
            try:
                text = rule.to_text()
                embedding = self._embedder.embed(text)
                self._vectorstore.add_documents(
                    collection_name="rules",
                    documents=[text],
                    embeddings=[embedding],
                    metadatas=[{"rule_id": rule_id, "source": source}],
                    ids=[rule_id],
                )
            except Exception:
                pass

        return rule

    def get(self, rule_id: str) -> Optional[TradingRule]:
        return self._rules.get(rule_id)

    def update(self, rule_id: str, **kwargs) -> bool:
        """규칙 필드 업데이트"""
        rule = self._rules.get(rule_id)
        if not rule:
            return False
        for k, v in kwargs.items():
            if hasattr(rule, k):
                setattr(rule, k, v)
        self._save()
        return True

    def record_hit(self, rule_id: str):
        """규칙 적중 기록"""
        rule = self._rules.get(rule_id)
        if rule:
            rule.hits += 1
            self._save()

    def record_miss(self, rule_id: str):
        """규칙 실패 기록"""
        rule = self._rules.get(rule_id)
        if rule:
            rule.misses += 1
            self._save()

    def deactivate(self, rule_id: str):
        """규칙 비활성화"""
        return self.update(rule_id, active=False)

    def get_active_rules(self) -> list[TradingRule]:
        """활성 규칙만 조회"""
        return [r for r in self._rules.values() if r.active]

    def search_by_condition(self, query: str, top_k: int = 5) -> list[TradingRule]:
        """
        조건 키워드로 규칙 검색.
        ChromaDB가 있으면 시맨틱 검색, 없으면 텍스트 매칭.
        """
        if self._embedder and self._vectorstore:
            try:
                query_vec = self._embedder.embed(query)
                results = self._vectorstore.query(
                    collection_name="rules",
                    query_embedding=query_vec,
                    n_results=top_k,
                )
                rule_ids = []
                for meta_list in results.get("metadatas", [[]]):
                    for meta in meta_list:
                        rid = meta.get("rule_id", "")
                        if rid:
                            rule_ids.append(rid)
                return [self._rules[rid] for rid in rule_ids
                        if rid in self._rules and self._rules[rid].active]
            except Exception:
                pass

        # 텍스트 매칭 폴백
        query_lower = query.lower()
        scored = []
        for rule in self.get_active_rules():
            text = rule.to_text().lower()
            # 단어 겹침 점수
            query_words = set(query_lower.split())
            text_words = set(text.split())
            overlap = len(query_words & text_words)
            if overlap > 0:
                scored.append((overlap, rule))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:top_k]]

    def get_stats(self) -> dict:
        """규칙 저장소 통계"""
        active = self.get_active_rules()
        return {
            "total_rules": len(self._rules),
            "active_rules": len(active),
            "avg_hit_rate": (
                sum(r.hit_rate for r in active) / len(active)
                if active else 0
            ),
            "avg_weight": (
                sum(r.weight for r in active) / len(active)
                if active else 0
            ),
            "sources": {
                source: sum(1 for r in self._rules.values() if r.source == source)
                for source in set(r.source for r in self._rules.values())
            } if self._rules else {},
        }

    @property
    def count(self) -> int:
        return len(self._rules)

    @property
    def active_count(self) -> int:
        return len(self.get_active_rules())
