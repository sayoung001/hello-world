"""
Phase A 모듈 동작 테스트
=========================
RAG (vectorstore, embedder, retriever, context_builder, ingesters)
Vision (chart_renderer, vision_scorer, cache)
"""

import sys
import os
import time
import tempfile

# 프로젝트 루트를 PYTHONPATH에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = {}


def _test_result(name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    RESULTS[name] = {"status": status, "detail": detail}
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


def test_embedder():
    """1. Embedder 테스트 — 텍스트 → 벡터 변환"""
    print("\n=== 1. Embedder 테스트 ===")
    try:
        from rag.embedder import Embedder
        emb = Embedder()

        # 단일 임베딩
        vec = emb.embed("BTC 가격이 급등했습니다")
        _test_result("embedder_single", len(vec) == 384,
                    f"차원: {len(vec)}")

        # 배치 임베딩
        texts = ["ETH 수렴 브레이크아웃", "BTC 하락 추세", "알트코인 펌프"]
        vecs = emb.embed_batch(texts)
        _test_result("embedder_batch", len(vecs) == 3 and len(vecs[0]) == 384,
                    f"배치 크기: {len(vecs)}, 차원: {len(vecs[0])}")

        # 유사도 테스트
        sim = emb.similarity("BTC 상승", "비트코인 가격 올랐다")
        sim2 = emb.similarity("BTC 상승", "오늘 날씨가 좋다")
        _test_result("embedder_similarity", sim > sim2,
                    f"관련 유사도: {sim:.4f} > 비관련: {sim2:.4f}")

        # 차원 수
        _test_result("embedder_dimension", emb.dimension == 384,
                    f"dimension: {emb.dimension}")

    except Exception as e:
        _test_result("embedder", False, str(e))


def test_vectorstore():
    """2. VectorStore 테스트 — ChromaDB CRUD"""
    print("\n=== 2. VectorStore 테스트 ===")
    try:
        from rag.vectorstore import VectorStoreManager
        from rag.embedder import Embedder

        emb = Embedder()

        # 임시 디렉토리에 DB 생성
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStoreManager(db_path=tmpdir)

            # 컬렉션 생성
            coll = store.get_collection("test_trades")
            _test_result("vectorstore_create", coll is not None,
                        f"컬렉션 생성 완료")

            # 문서 추가
            docs = [
                "ETHUSDT 롱 브레이크아웃, GAP 0.5%, TP 도달, ROE +150%",
                "BTCUSDT 숏 브레이크아웃, GAP 0.3%, SL 히트, ROE -100%",
                "SOLUSDT 롱 브레이크아웃, GAP 0.8%, TP 도달, ROE +200%",
            ]
            embeddings = emb.embed_batch(docs)
            metadatas = [
                {"symbol": "ETHUSDT", "direction": "long", "exit_type": "TP", "roe": 150.0, "gap": 0.5, "timestamp": "2025-06-01"},
                {"symbol": "BTCUSDT", "direction": "short", "exit_type": "SL", "roe": -100.0, "gap": 0.3, "timestamp": "2025-07-01"},
                {"symbol": "SOLUSDT", "direction": "long", "exit_type": "TP", "roe": 200.0, "gap": 0.8, "timestamp": "2025-08-01"},
            ]
            store.add_documents("test_trades", docs, embeddings, metadatas,
                                ["t1", "t2", "t3"])

            cnt = store.count("test_trades")
            _test_result("vectorstore_add", cnt == 3,
                        f"문서 수: {cnt}")

            # 검색
            query_vec = emb.embed("ETH 롱 브레이크아웃 수렴")
            results = store.query("test_trades", query_vec, n_results=2)
            found_docs = results.get("documents", [[]])[0]
            _test_result("vectorstore_query", len(found_docs) >= 1,
                        f"검색 결과: {len(found_docs)}건, 첫번째: {found_docs[0][:50]}...")

            # 검색 정확성 (ETH 관련이 최상위여야 함)
            first_meta = results.get("metadatas", [[]])[0][0] if results.get("metadatas", [[]])[0] else {}
            _test_result("vectorstore_relevance",
                        first_meta.get("symbol") == "ETHUSDT",
                        f"최상위 결과: {first_meta.get('symbol')}")

            # health check
            health = store.health_check()
            _test_result("vectorstore_health", health["status"] == "ok",
                        f"상태: {health}")

    except Exception as e:
        _test_result("vectorstore", False, str(e))


def test_retriever():
    """3. Retriever 테스트 — 시맨틱 검색"""
    print("\n=== 3. Retriever 테스트 ===")
    try:
        from rag.vectorstore import VectorStoreManager
        from rag.embedder import Embedder
        from rag.retriever import TradeContextRetriever

        emb = Embedder()

        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStoreManager(db_path=tmpdir)

            # 테스트 데이터 적재
            docs = [
                "ETHUSDT EMA 수렴 후 롱 브레이크아웃, GAP 0.5%, confidence 70, ADX 35. 결과: TP, ROE +180%, 32캔들 보유.",
                "ETHUSDT EMA 수렴 후 숏 브레이크아웃, GAP 0.4%, confidence 60, ADX 30. 결과: SL, ROE -100%, 8캔들 보유.",
                "BTCUSDT EMA 수렴 후 롱 브레이크아웃, GAP 0.3%, confidence 80, ADX 40. 결과: TP, ROE +120%, 45캔들 보유.",
                "SOLUSDT EMA 수렴 후 롱 브레이크아웃, GAP 0.7%, confidence 55, ADX 28. 결과: Timeout, ROE +20%, 96캔들 보유.",
            ]
            embeddings = emb.embed_batch(docs)
            metadatas = [
                {"symbol": "ETHUSDT", "direction": "long", "exit_type": "TP", "roe": 180.0, "gap": 0.5, "timestamp": str(time.time() - 86400*30)},
                {"symbol": "ETHUSDT", "direction": "short", "exit_type": "SL", "roe": -100.0, "gap": 0.4, "timestamp": str(time.time() - 86400*60)},
                {"symbol": "BTCUSDT", "direction": "long", "exit_type": "TP", "roe": 120.0, "gap": 0.3, "timestamp": str(time.time() - 86400*10)},
                {"symbol": "SOLUSDT", "direction": "long", "exit_type": "Timeout", "roe": 20.0, "gap": 0.7, "timestamp": str(time.time() - 86400*90)},
            ]
            store.add_documents("trades", docs, embeddings, metadatas,
                                ["tr1", "tr2", "tr3", "tr4"])

            # 시그널 검색
            retriever = TradeContextRetriever(store, emb)
            signal = {"symbol": "ETHUSDT", "direction": "long", "gap": 0.5,
                      "confidence": 65, "adx": 32}
            results = retriever.retrieve_similar_trades(signal, top_k=3)

            _test_result("retriever_search", len(results) >= 1,
                        f"결과 수: {len(results)}")

            if results:
                top = results[0]
                _test_result("retriever_similarity",
                            top["similarity"] > 0.5,
                            f"최고 유사도: {top['similarity']}, final_score: {top['final_score']}")

                _test_result("retriever_recency",
                            top.get("recency_weight", 0) > 0,
                            f"시간 가중치: {top.get('recency_weight')}")

    except Exception as e:
        _test_result("retriever", False, str(e))


def test_context_builder():
    """4. ContextBuilder 테스트 — 검색 결과 → 피처/텍스트"""
    print("\n=== 4. ContextBuilder 테스트 ===")
    try:
        from rag.context_builder import ContextBuilder

        cb = ContextBuilder()

        trade_results = [
            {"document": "ETH 롱 TP", "metadata": {"symbol": "ETHUSDT", "exit_type": "TP", "roe": 150}, "similarity": 0.85},
            {"document": "ETH 숏 SL", "metadata": {"symbol": "ETHUSDT", "exit_type": "SL", "roe": -100}, "similarity": 0.70},
            {"document": "BTC 롱 TP", "metadata": {"symbol": "BTCUSDT", "exit_type": "TP", "roe": 120}, "similarity": 0.65},
        ]

        # RL 피처
        rl_features = cb.build_for_rl(trade_results)
        _test_result("context_rl_winrate",
                    abs(rl_features["similar_trade_winrate"] - 0.6667) < 0.01,
                    f"승률: {rl_features['similar_trade_winrate']}")
        _test_result("context_rl_roe",
                    rl_features["similar_trade_avg_roe"] > 0,
                    f"평균 ROE: {rl_features['similar_trade_avg_roe']}")
        _test_result("context_rl_sl_rate",
                    abs(rl_features["historical_sl_hit_rate"] - 0.3333) < 0.01,
                    f"SL 히트율: {rl_features['historical_sl_hit_rate']}")

        # LLM 텍스트
        llm_text = cb.build_for_llm(trade_results)
        _test_result("context_llm_text",
                    "유사 거래" in llm_text and "승률" in llm_text,
                    f"텍스트 길이: {len(llm_text)}자")

    except Exception as e:
        _test_result("context_builder", False, str(e))


def test_news_ingester():
    """5. NewsIngester 테스트 — CryptoCompare 뉴스 수집"""
    print("\n=== 5. NewsIngester 테스트 ===")
    try:
        from rag.vectorstore import VectorStoreManager
        from rag.embedder import Embedder
        from rag.news_ingester import NewsIngester

        emb = Embedder()

        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStoreManager(db_path=tmpdir)
            ingester = NewsIngester(store, emb)

            # CryptoCompare 뉴스 수집 (CryptoPanic 토큰 없이)
            count = ingester.ingest_cycle()
            _test_result("news_ingester_cycle", count >= 0,
                        f"수집 건수: {count}")

            stored = ingester.stored_count
            _test_result("news_ingester_stored", stored == count,
                        f"저장 건수: {stored}")

    except Exception as e:
        _test_result("news_ingester", False, str(e))


def test_chart_renderer():
    """6. ChartRenderer 테스트 — 차트 PNG 렌더링"""
    print("\n=== 6. ChartRenderer 테스트 ===")
    try:
        from vision.chart_renderer import ChartRenderer
        import numpy as np

        renderer = ChartRenderer()

        # 가상 OHLCV 데이터 생성 (50캔들)
        base_ts = int(time.time() * 1000) - 50 * 900000
        base_price = 100000.0
        ohlcv = []
        for i in range(50):
            ts = base_ts + i * 900000
            change = np.random.normal(0, 0.5)
            o = base_price + change
            h = o + abs(np.random.normal(0, 0.3))
            l = o - abs(np.random.normal(0, 0.3))
            c = o + np.random.normal(0, 0.2)
            v = abs(np.random.normal(1000, 200))
            ohlcv.append([ts, o, h, l, c, v])
            base_price = c

        # 렌더링
        img_bytes = renderer.render(ohlcv, symbol="BTCUSDT")
        _test_result("chart_render_bytes", len(img_bytes) > 1000,
                    f"이미지 크기: {len(img_bytes)} bytes")

        # PNG 헤더 검증
        _test_result("chart_render_png",
                    img_bytes[:4] == b'\x89PNG',
                    "PNG 헤더 확인")

        # 파일 저장 테스트
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = renderer.render_to_file(ohlcv, f.name, symbol="BTCUSDT",
                                           sl_price=base_price * 0.97,
                                           tp_price=base_price * 1.05)
            size = os.path.getsize(path)
            _test_result("chart_render_file", size > 1000,
                        f"파일 크기: {size} bytes, 경로: {path}")
            os.unlink(path)

    except Exception as e:
        _test_result("chart_renderer", False, str(e))


def test_vision_scorer():
    """7. VisionScorer 테스트 — 패턴 분석 → 수치 피처"""
    print("\n=== 7. VisionScorer 테스트 ===")
    try:
        from vision.vision_scorer import VisionScorer

        scorer = VisionScorer()

        # 정상 분석 결과
        analysis = {
            "patterns": [{"name": "삼각수렴", "confidence": 75, "phase": "말기"}],
            "trend_strength": 0.6,
            "breakout_prediction": "LONG",
            "chart_based_sl": 95000.0,
            "risk_assessment": "low",
        }

        features = scorer.to_state_features(analysis, signal_direction="long")
        _test_result("scorer_trend", features["vision_trend"] == 0.6,
                    f"trend: {features['vision_trend']}")
        _test_result("scorer_conf", features["vision_pattern_conf"] == 0.75,
                    f"pattern_conf: {features['vision_pattern_conf']}")
        _test_result("scorer_align", features["vision_breakout_align"] == 1.0,
                    f"breakout_align: {features['vision_breakout_align']}")
        _test_result("scorer_risk", features["vision_risk_level"] == 0.2,
                    f"risk_level: {features['vision_risk_level']}")

        # 반대 방향 시그널
        features_opp = scorer.to_state_features(analysis, signal_direction="short")
        _test_result("scorer_opposite", features_opp["vision_breakout_align"] == -1.0,
                    f"반대 align: {features_opp['vision_breakout_align']}")

        # confidence 조정
        adj = scorer.compute_confidence_adjustment(features)
        _test_result("scorer_adjustment", adj > 0,
                    f"일치 시 조정: +{adj}")

        adj_opp = scorer.compute_confidence_adjustment(features_opp)
        _test_result("scorer_adj_opposite", adj_opp < 0,
                    f"반대 시 조정: {adj_opp}")

        # 에러 케이스
        empty = scorer.to_state_features({"parse_error": True})
        _test_result("scorer_empty", empty["vision_trend"] == 0.0,
                    "에러 시 기본값 반환")

    except Exception as e:
        _test_result("vision_scorer", False, str(e))


def test_vision_cache():
    """8. VisionCache 테스트 — 캐시 동작"""
    print("\n=== 8. VisionCache 테스트 ===")
    try:
        from vision.cache import VisionCache

        cache = VisionCache(ttl=2, max_size=3)  # TTL 2초, 최대 3건

        # 저장 및 조회
        cache.put("BTCUSDT", 1000000, {"trend": 0.5})
        result = cache.get("BTCUSDT", 1000000)
        _test_result("cache_put_get", result is not None and result["trend"] == 0.5,
                    f"캐시 히트: {result}")

        # 미존재 키
        result_miss = cache.get("ETHUSDT", 1000000)
        _test_result("cache_miss", result_miss is None,
                    "존재하지 않는 키 → None")

        # TTL 만료
        time.sleep(2.5)
        result_expired = cache.get("BTCUSDT", 1000000)
        _test_result("cache_ttl", result_expired is None,
                    "TTL 만료 후 → None")

        # LRU 정리
        cache_lru = VisionCache(ttl=60, max_size=2)
        cache_lru.put("A", 100, {"a": 1})
        cache_lru.put("B", 200, {"b": 2})
        cache_lru.put("C", 300, {"c": 3})  # A가 제거되어야 함
        _test_result("cache_lru", cache_lru.size == 2 and cache_lru.get("A", 100) is None,
                    f"LRU 정리 후 크기: {cache_lru.size}")

        # 통계
        stats = cache_lru.stats()
        _test_result("cache_stats", "total_entries" in stats,
                    f"통계: {stats}")

    except Exception as e:
        _test_result("vision_cache", False, str(e))


def test_max_tokens_fix():
    """9. base.py max_tokens 수정 확인"""
    print("\n=== 9. max_tokens 수정 확인 ===")
    try:
        import inspect
        from agents.core.base import AgentBase

        sig = inspect.signature(AgentBase.llm_call)
        default_max = sig.parameters["max_tokens"].default
        _test_result("max_tokens_llm_call", default_max == 4096,
                    f"llm_call max_tokens 기본값: {default_max}")

        sig2 = inspect.signature(AgentBase.llm_json)
        default_max2 = sig2.parameters["max_tokens"].default
        _test_result("max_tokens_llm_json", default_max2 == 4096,
                    f"llm_json max_tokens 기본값: {default_max2}")

    except Exception as e:
        _test_result("max_tokens", False, str(e))


def main():
    print("=" * 60)
    print("Phase A 모듈 동작 테스트")
    print("=" * 60)

    test_embedder()
    test_vectorstore()
    test_retriever()
    test_context_builder()
    test_news_ingester()
    test_chart_renderer()
    test_vision_scorer()
    test_vision_cache()
    test_max_tokens_fix()

    # 요약
    print("\n" + "=" * 60)
    print("테스트 결과 요약")
    print("=" * 60)
    passed = sum(1 for v in RESULTS.values() if v["status"] == "PASS")
    failed = sum(1 for v in RESULTS.values() if v["status"] == "FAIL")
    print(f"총 {len(RESULTS)}건: PASS {passed}, FAIL {failed}")

    if failed > 0:
        print("\n실패 항목:")
        for name, v in RESULTS.items():
            if v["status"] == "FAIL":
                print(f"  - {name}: {v['detail']}")

    return RESULTS


if __name__ == "__main__":
    main()
