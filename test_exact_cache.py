"""验证相同问题的缓存闭环；独立内存数据库，模拟 Redis 和模型网络调用。"""

import unittest
from dataclasses import replace
from unittest.mock import MagicMock, patch

from redis.exceptions import ConnectionError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import cache_context
import rag_service
import semantic_cache
from cache_models import CacheEntry
from database import Base
from model import Paper, PaperChunk, PaperDocument, ResearchTask, ResearchTaskPaper
from schemas import ResearchEntityResponse, ResearchTaskAnalysisResponse


class TestExactCache(unittest.TestCase):
    """真实执行范围计算、缓存序列化和原 RAG，只替换外部网络边界。"""

    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.addCleanup(self.db.close)

        self.paper = Paper(title="缓存方法研究", author="作者", year=2025)
        self.task = ResearchTask(title="缓存测试", question="这个方法是什么？")
        self.db.add_all([self.paper, self.task])
        self.db.flush()
        self.db.add(ResearchTaskPaper(task_id=self.task.id, paper_id=self.paper.id))
        document = PaperDocument(
            paper_id=self.paper.id, original_name="test.pdf", stored_name="test.pdf",
            file_path="/unused/test.pdf", content_type="application/pdf", file_size=1,
        )
        self.db.add(document)
        self.db.flush()
        self.chunk = PaperChunk(
            document_id=document.id, page_number=2, chunk_index=0,
            text="本方法按问题查找缓存。", char_count=11,
        )
        self.db.add(self.chunk)
        self.db.commit()

        # 只模拟 Redis 的命令边界，真实缓存读写函数仍然执行。
        self.storage = {}
        self.redis = MagicMock()
        self.redis.get.side_effect = self.storage.get
        self.redis.set.side_effect = self.store
        self.start_patch("semantic_cache.redis_client", self.redis)
        self.rewrite = self.start_patch(
            "rag_service.query_rewriter.rewrite_query_for_search",
            return_value="cache method",
        )
        self.search = self.start_patch(
            "rag_service.semantic_search.search_task_chunks_semantic",
            return_value=[(self.chunk, self.paper, 0.9)],
        )
        self.generate = self.start_patch(
            "rag_service.generate_text", return_value="按问题查找缓存[资料1]。",
        )

    def start_patch(self, target, *args, **kwargs):
        patcher = patch(target, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def store(self, key, value, ex):
        self.storage[key] = value
        return True

    def ask(self, question="这个方法是什么？", top_k=5, task_id=None):
        return rag_service.answer_research_question(
            self.db, self.task.id if task_id is None else task_id, question, top_k,
        )

    def only_entry(self):
        self.assertEqual(len(self.storage), 1)
        key, raw = next(iter(self.storage.items()))
        return key, CacheEntry.model_validate_json(raw)

    def test_same_question_skips_rewrite_search_and_generation(self):
        first = self.ask()
        second = self.ask()
        self.assertEqual(first, second)
        self.assertIsInstance(second.sources[0], rag_service.RAGSource)
        self.rewrite.assert_called_once()
        self.search.assert_called_once()
        self.generate.assert_called_once()
        self.redis.set.assert_called_once()

    def test_first_answer_saves_complete_record_with_expiration(self):
        result = self.ask()
        key, entry = self.only_entry()
        self.assertEqual(entry.answer, result.answer)
        self.assertEqual(entry.question, result.question)
        self.assertEqual(entry.sources[0].chunk_id, self.chunk.id)
        self.assertEqual(entry.sources[0].page_number, 2)
        self.assertEqual(entry.features.entities, [])
        self.assertEqual(entry.expires_at - entry.created_at, semantic_cache.CACHE_TTL_SECONDS)
        self.assertGreaterEqual(entry.generation_seconds, 0)
        self.assertTrue(key.endswith(":" + entry.id))
        ttl = self.redis.set.call_args.kwargs["ex"]
        self.assertGreater(ttl, 0)
        self.assertLessEqual(ttl, semantic_cache.CACHE_TTL_SECONDS)

    def test_surrounding_whitespace_still_hits(self):
        self.ask()
        result = self.ask("  这个方法是什么？  ")
        self.assertEqual(result.question, "这个方法是什么？")
        self.generate.assert_called_once()

    def test_different_question_generates_again(self):
        self.ask()
        self.ask("这个方法有什么局限？")
        self.assertEqual(self.generate.call_count, 2)
        self.assertEqual(len(self.storage), 2)

    def test_different_top_k_does_not_reuse(self):
        self.ask(top_k=5)
        self.ask(top_k=3)
        self.assertEqual(self.generate.call_count, 2)

    def test_different_task_with_same_paper_does_not_reuse(self):
        self.ask()
        other = ResearchTask(title="另一个任务", question=self.task.question)
        self.db.add(other)
        self.db.flush()
        self.db.add(ResearchTaskPaper(task_id=other.id, paper_id=self.paper.id))
        self.db.commit()
        self.ask(task_id=other.id)
        self.assertEqual(self.generate.call_count, 2)

    def test_changed_chunk_text_changes_scope_and_regenerates(self):
        self.ask()
        self.chunk.text = "修订版增加了知识约束。"
        self.db.commit()
        self.ask()
        self.assertEqual(self.generate.call_count, 2)
        self.assertEqual(len(self.storage), 2)

    def test_changed_paper_title_does_not_reuse_old_title(self):
        self.ask()
        self.paper.title = "修订后的标题"
        self.db.commit()
        result = self.ask()
        self.assertEqual(self.generate.call_count, 2)
        self.assertEqual(result.sources[0].paper_title, "修订后的标题")

    def test_changed_model_changes_scope(self):
        self.ask()
        settings = replace(cache_context.settings, deepseek_model="other-model")
        with patch("cache_context.settings", settings):
            self.ask()
        self.assertEqual(self.generate.call_count, 2)

    def test_expired_record_regenerates_without_sleep(self):
        self.ask()
        key, entry = self.only_entry()
        entry.expires_at = 0
        self.storage[key] = entry.model_dump_json()
        self.ask()
        self.assertEqual(self.generate.call_count, 2)

    def test_malformed_json_falls_back_to_generation(self):
        self.ask()
        key, _ = self.only_entry()
        self.storage[key] = "不是JSON"
        self.ask()
        self.assertEqual(self.generate.call_count, 2)

    def test_mismatched_page_regenerates(self):
        self.ask()
        key, entry = self.only_entry()
        entry.sources[0].page_number = 99
        self.storage[key] = entry.model_dump_json()
        self.ask()
        self.assertEqual(self.generate.call_count, 2)

    def test_missing_source_regenerates(self):
        self.ask()
        key, entry = self.only_entry()
        entry.sources[0].chunk_id = 99999
        self.storage[key] = entry.model_dump_json()
        self.ask()
        self.assertEqual(self.generate.call_count, 2)

    def test_empty_sources_are_not_reused(self):
        self.ask()
        key, entry = self.only_entry()
        entry.sources = []
        self.storage[key] = entry.model_dump_json()
        self.ask()
        self.assertEqual(self.generate.call_count, 2)

    def test_redis_read_and_write_failure_still_returns_answer(self):
        self.redis.get.side_effect = ConnectionError("模拟Redis不可用")
        self.redis.set.side_effect = ConnectionError("模拟Redis不可用")
        result = self.ask()
        self.assertEqual(result.answer, "按问题查找缓存[资料1]。")
        self.assertEqual(result.sources[0].chunk_id, self.chunk.id)
        self.generate.assert_called_once()

    def test_write_failure_does_not_discard_generated_answer(self):
        self.redis.set.side_effect = ConnectionError("模拟保存失败")
        result = self.ask()
        self.assertEqual(result.answer, "按问题查找缓存[资料1]。")
        self.assertEqual(self.storage, {})

    def test_expired_entry_is_not_written(self):
        self.ask()
        _, entry = self.only_entry()
        entry.expires_at = 0
        self.redis.set.reset_mock()
        self.assertFalse(semantic_cache.save_cache_entry("expired", entry))
        self.redis.set.assert_not_called()

    def test_generation_failure_is_not_saved(self):
        self.generate.side_effect = RuntimeError("模拟模型调用失败")
        with self.assertRaisesRegex(RuntimeError, "模拟模型调用失败"):
            self.ask()
        self.redis.set.assert_not_called()
        self.assertEqual(self.storage, {})

    def test_invalid_input_does_not_read_cache_or_generate(self):
        for question, top_k in (("  ", 5), ("问题", 0)):
            with self.subTest(question=question, top_k=top_k):
                with self.assertRaises(ValueError):
                    self.ask(question, top_k)
        self.redis.get.assert_not_called()
        self.generate.assert_not_called()

    def prepare_semantic_cache(self):
        """保存实体目录，真实执行特征提取；模拟向量模型和 Redis 搜索响应。"""
        self.task.result = ResearchTaskAnalysisResponse(
            task_id=self.task.id, question=self.task.question, summary="分析",
            methods=[], experiments=[], conclusions=[], limitations=[],
            sources=[dict(
                paper_id=self.paper.id, paper_title=self.paper.title,
                chunk_id=self.chunk.id, page_number=2, score=0.9,
            )],
            entities=[ResearchEntityResponse(
                canonical_name=name, entity_type="algorithm", source_numbers=[1],
            ) for name in ("KCSC-B", "LRU")],
        ).model_dump_json()
        self.db.commit()
        self.embed = self.start_patch(
            "rag_service.embedding_service.embed_texts", return_value=[[1.0, 0.0, 0.0]],
        )
        self.redis.execute_command.return_value = []
        self.ask("KCSC-B如何决策？")
        key, entry = self.only_entry()
        self.redis.execute_command.return_value = [entry.id, "0.999"]
        return key, entry

    def test_semantic_hit_skips_generation_and_preserves_current_question(self):
        self.prepare_semantic_cache()
        result = self.ask("KCSC-B的决策机制是什么？")
        self.assertEqual(result.question, "KCSC-B的决策机制是什么？")
        self.assertEqual(result.answer, "按问题查找缓存[资料1]。")
        self.assertEqual(result.sources[0].chunk_id, self.chunk.id)
        self.generate.assert_called_once()
        self.search.assert_called_once()
        self.assertEqual(self.embed.call_count, 2)

    def test_semantic_miss_saves_vector_with_same_answer_id(self):
        _, entry = self.prepare_semantic_cache()
        pipe = self.redis.pipeline.return_value.__enter__.return_value
        args = pipe.execute_command.call_args.args
        self.assertEqual(args[0], "VADD")
        self.assertIn(entry.id, args)
        pipe.expire.assert_called_once_with(args[1], semantic_cache.CACHE_TTL_SECONDS)
        pipe.execute.assert_called_once()

    def test_semantic_conditions_must_match_even_with_high_similarity(self):
        self.prepare_semantic_cache()
        for question in (
            "LRU如何决策？", "KCSC-B有什么局限？",
            "KCSC-B如何处理10条请求？", "KCSC-B不能如何决策？",
        ):
            with self.subTest(question=question):
                before = self.generate.call_count
                self.ask(question)
                self.assertEqual(self.generate.call_count, before + 1)

    def test_semantic_low_score_is_not_reused(self):
        _, entry = self.prepare_semantic_cache()
        self.redis.execute_command.return_value = [entry.id, "0.90"]
        self.ask("KCSC-B的决策机制是什么？")
        self.assertEqual(self.generate.call_count, 2)

    def test_semantic_expired_entry_is_not_reused(self):
        key, entry = self.prepare_semantic_cache()
        entry.expires_at = 0
        self.storage[key] = entry.model_dump_json()
        self.ask("KCSC-B的决策机制是什么？")
        self.assertEqual(self.generate.call_count, 2)

    def test_semantic_bad_source_is_not_reused(self):
        key, entry = self.prepare_semantic_cache()
        entry.sources[0].page_number = 999
        self.storage[key] = entry.model_dump_json()
        self.ask("KCSC-B的决策机制是什么？")
        self.assertEqual(self.generate.call_count, 2)

    def test_semantic_embedding_failure_falls_back_to_rag(self):
        from urllib.error import URLError
        self.prepare_semantic_cache()
        self.embed.side_effect = URLError("模拟向量服务错误")
        result = self.ask("KCSC-B的决策机制是什么？")
        self.assertIsInstance(result, rag_service.RAGResult)
        self.assertEqual(self.generate.call_count, 2)

    def test_semantic_search_failure_falls_back_to_rag(self):
        self.prepare_semantic_cache()
        self.redis.execute_command.side_effect = ConnectionError("模拟向量检索错误")
        self.ask("KCSC-B的决策机制是什么？")
        self.assertEqual(self.generate.call_count, 2)

    def test_exact_hit_does_not_generate_question_embedding(self):
        self.prepare_semantic_cache()
        self.embed.reset_mock()
        self.ask("KCSC-B如何决策？")
        self.embed.assert_not_called()
        self.generate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
