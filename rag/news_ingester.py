"""
news_ingester.py — 뉴스 수집 → ChromaDB 벡터 저장
====================================================
CryptoPanic API (무료 300건/일) + CryptoCompare News API.
15분 주기 수집 → 임베딩 → ChromaDB 저장.
"""

from __future__ import annotations
import hashlib
import time
from datetime import datetime, timezone, timedelta

import requests

from rag.vectorstore import VectorStoreManager
from rag.embedder import Embedder

KST = timezone(timedelta(hours=9))

# CryptoPanic API (무료 티어)
CRYPTOPANIC_BASE = "https://cryptopanic.com/api/free/v1/posts/"

# CryptoCompare News API
CRYPTOCOMPARE_NEWS = "https://min-api.cryptocompare.com/data/v2/news/"


class NewsIngester:
    """
    뉴스 수집 → 임베딩 → ChromaDB 저장.

    지원 소스:
    - CryptoPanic (무료 티어)
    - CryptoCompare News API
    """

    def __init__(self, store: VectorStoreManager, embedder: Embedder,
                 cryptopanic_token: str = ""):
        self.store = store
        self.embedder = embedder
        self.cryptopanic_token = cryptopanic_token
        self._seen_ids: set[str] = set()  # 중복 방지

    def ingest_cycle(self) -> int:
        """
        한 사이클 뉴스 수집 + 저장.
        Returns: 새로 저장된 문서 수.
        """
        articles = []

        # CryptoPanic
        if self.cryptopanic_token:
            articles.extend(self._fetch_cryptopanic())

        # CryptoCompare
        articles.extend(self._fetch_cryptocompare())

        if not articles:
            return 0

        # 중복 제거
        new_articles = []
        for art in articles:
            aid = art["id"]
            if aid not in self._seen_ids:
                self._seen_ids.add(aid)
                new_articles.append(art)

        if not new_articles:
            return 0

        # 임베딩 생성
        texts = [a["text"] for a in new_articles]
        embeddings = self.embedder.embed_batch(texts)

        # ChromaDB 저장
        self.store.add_documents(
            collection_name="news",
            documents=texts,
            embeddings=embeddings,
            metadatas=[a["metadata"] for a in new_articles],
            ids=[a["id"] for a in new_articles],
        )

        return len(new_articles)

    def _fetch_cryptopanic(self) -> list[dict]:
        """CryptoPanic API에서 뉴스 수집"""
        try:
            params = {
                "auth_token": self.cryptopanic_token,
                "kind": "news",
                "filter": "important",
            }
            resp = requests.get(CRYPTOPANIC_BASE, params=params, timeout=10)
            if not resp.ok:
                return []

            data = resp.json()
            results = data.get("results", [])
            articles = []

            for item in results:
                title = item.get("title", "")
                source = item.get("source", {}).get("title", "unknown")
                created = item.get("created_at", "")
                currencies = item.get("currencies", [])
                symbols = [c.get("code", "") for c in currencies] if currencies else []

                # 감정 분석 (CryptoPanic 자체)
                votes = item.get("votes", {})
                positive = votes.get("positive", 0)
                negative = votes.get("negative", 0)
                sentiment = 0.0
                if positive + negative > 0:
                    sentiment = (positive - negative) / (positive + negative)

                aid = hashlib.md5(
                    f"cp_{item.get('id', '')}".encode()
                ).hexdigest()

                articles.append({
                    "id": aid,
                    "text": title,
                    "metadata": {
                        "symbol": ",".join(symbols) if symbols else "GENERAL",
                        "source": f"CryptoPanic/{source}",
                        "timestamp": created,
                        "sentiment_score": round(sentiment, 4),
                    },
                })
            return articles

        except Exception as e:
            print(f"[RAG/뉴스] CryptoPanic 수집 실패: {e}")
            return []

    def _fetch_cryptocompare(self) -> list[dict]:
        """CryptoCompare News API에서 뉴스 수집"""
        try:
            params = {"lang": "EN", "sortOrder": "latest"}
            resp = requests.get(CRYPTOCOMPARE_NEWS, params=params, timeout=10)
            if not resp.ok:
                return []

            data = resp.json()
            items = data.get("Data", [])
            if isinstance(items, dict):
                items = list(items.values())

            articles = []
            for item in items:
                title = item.get("title", "")
                body = item.get("body", "")[:200]  # 본문 앞 200자
                source = item.get("source", "unknown")
                published = item.get("published_on", 0)
                categories = item.get("categories", "")

                # 심볼 추출 (카테고리에서)
                symbol = "GENERAL"
                if categories:
                    cats = categories.split("|")
                    crypto_cats = [c.strip() for c in cats if len(c.strip()) <= 5]
                    if crypto_cats:
                        symbol = ",".join(crypto_cats[:3])

                text = f"{title}. {body}" if body else title
                aid = hashlib.md5(
                    f"cc_{item.get('id', published)}".encode()
                ).hexdigest()

                articles.append({
                    "id": aid,
                    "text": text,
                    "metadata": {
                        "symbol": symbol,
                        "source": f"CryptoCompare/{source}",
                        "timestamp": str(published),
                        "sentiment_score": 0.0,  # CryptoCompare는 자체 센티먼트 없음
                    },
                })
            return articles

        except Exception as e:
            print(f"[RAG/뉴스] CryptoCompare 수집 실패: {e}")
            return []

    @property
    def stored_count(self) -> int:
        """저장된 뉴스 문서 수"""
        return self.store.count("news")
