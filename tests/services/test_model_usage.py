"""模型调用与用量日志联调，使用独立目录和模拟SDK，不请求外部服务。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import httpx2 as httpx
from openai import APIConnectionError, APIStatusError
from research_library.core import llm_service, model_usage


class TestModelUsage(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(TemporaryDirectory())
        self.path = Path(self.directory) / 'llm_usage.jsonl'
        self.enterContext(patch.object(model_usage, 'settings', SimpleNamespace(
            log_dir=Path(self.directory), deepseek_model='test-model')))
        self.enterContext(patch.object(llm_service, 'get_deepseek_api_key', return_value='secret-test-key'))
        self.sdk = MagicMock()
        self.sdk.__enter__.return_value = self.sdk
        self.enterContext(patch.object(llm_service, 'OpenAI', return_value=self.sdk))
        self.response = SimpleNamespace(
            id='test-response', model='test-model',
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20, total_tokens=120),
            choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='输出正文保密'))],
        )
        self.sdk.chat.completions.create.return_value = self.response

    def rows(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def test_text_and_tool_calls_append_separate_records_without_changing_results(self):
        text = llm_service.generate_text('系统内容保密', '用户问题保密')
        message = llm_service.generate_tool_message([{'role': 'user', 'content': '用户问题保密'}], [])
        self.assertEqual(text, '输出正文保密')
        self.assertIs(message, self.response.choices[0].message)
        rows = self.rows()
        self.assertEqual([r['call_type'] for r in rows], ['text', 'tool'])
        for row in rows:
            self.assertEqual((row['input_tokens'], row['output_tokens'], row['total_tokens']), (100, 20, 120))
            self.assertGreaterEqual(row['elapsed_seconds'], 0)
            self.assertEqual(row['status'], 'api_returned')
        raw = self.path.read_text()
        for secret in ('系统内容保密', '用户问题保密', '输出正文保密', 'secret-test-key'):
            self.assertNotIn(secret, raw)

    def test_summary_disables_tool_selection_in_sdk_request(self):
        llm_service.generate_tool_message([], [], tool_choice="none")
        self.assertEqual(self.sdk.chat.completions.create.call_args.kwargs["tool_choice"], "none")

    def test_missing_usage_is_unknown_not_zero(self):
        self.response.usage = None
        llm_service.generate_text('系统', '问题')
        row = self.rows()[0]
        self.assertIsNone(row['input_tokens'])
        self.assertIsNone(row['output_tokens'])
        self.assertIsNone(row['total_tokens'])

    def test_truncated_text_and_tool_responses_still_record_usage(self):
        self.response.choices[0].finish_reason = 'length'
        for generate in (lambda: llm_service.generate_text('系统', '问题'),
                         lambda: llm_service.generate_tool_message([], [])):
            with self.assertRaises(llm_service.ModelOutputError):
                generate()
        self.assertEqual([r['finish_reason'] for r in self.rows()], ['length', 'length'])
        self.assertEqual([r['total_tokens'] for r in self.rows()], [120, 120])

    def test_connection_failure_is_recorded_for_both_entrypoints_without_error_body(self):
        request = httpx.Request('POST', 'https://example.invalid/private')
        self.sdk.chat.completions.create.side_effect = APIConnectionError(
            message='不应记录的错误正文', request=request)
        for generate in (lambda: llm_service.generate_text('系统', '问题'),
                         lambda: llm_service.generate_tool_message([], [])):
            with self.assertRaisesRegex(RuntimeError, '无法连接'):
                generate()
        for row in self.rows():
            self.assertEqual(row['status'], 'request_failed')
            self.assertEqual(row['error_type'], 'APIConnectionError')
            self.assertIsNone(row['total_tokens'])
        self.assertNotIn('不应记录的错误正文', self.path.read_text())
        self.assertNotIn('example.invalid', self.path.read_text())

    def test_api_status_error_preserves_existing_exception_and_unknown_usage(self):
        response = httpx.Response(429, request=httpx.Request('POST', 'https://example.invalid'))
        self.sdk.chat.completions.create.side_effect = APIStatusError('内部错误信息', response=response, body=None)
        with self.assertRaisesRegex(RuntimeError, '429'):
            llm_service.generate_text('系统', '问题')
        self.assertEqual(self.rows()[0]['error_type'], 'APIStatusError')
        self.assertIsNone(self.rows()[0]['input_tokens'])

    def test_log_write_failure_does_not_discard_model_result(self):
        with patch.object(Path, 'open', side_effect=OSError('disk full')), \
             self.assertLogs(model_usage.__name__, level='WARNING'):
            self.assertEqual(llm_service.generate_text('系统', '问题'), '输出正文保密')

    def test_empty_choices_are_recorded_before_existing_validation(self):
        self.response.choices = []
        with self.assertRaisesRegex(RuntimeError, '没有返回回答'):
            llm_service.generate_text('系统', '问题')
        self.assertIsNone(self.rows()[0]['finish_reason'])
        self.assertEqual(self.rows()[0]['total_tokens'], 120)

    def test_nested_trace_scopes_restore_tags_and_link_saved_investigation(self):
        with model_usage.model_usage_scope(trace_id='trace-a', task_id=6, report_job_id=21, claim_id='claim-a'):
            with model_usage.model_usage_scope(stage='react', round_number=1):
                llm_service.generate_tool_message([{'role': 'user', 'content': '测试'}], [])
            with model_usage.model_usage_scope(stage='review'):
                llm_service.generate_text('系统', '问题')
            model_usage.record_investigation_saved(7)
        llm_service.generate_text('独立调用', '问题')
        rows = self.rows()
        self.assertEqual(rows[0]['round_number'], 1)
        self.assertEqual(rows[0]['stage'], 'react')
        self.assertEqual(rows[1]['stage'], 'review')
        self.assertNotIn('round_number', rows[1])
        self.assertEqual(rows[2]['event'], 'investigation_saved')
        self.assertEqual(rows[2]['investigation_id'], 7)
        self.assertEqual([row['trace_id'] for row in rows[:3]], ['trace-a'] * 3)
        self.assertNotIn('trace_id', rows[3])
        self.assertEqual(rows[0]['message_count'], 1)
        self.assertEqual(rows[1]['message_count'], 2)

    def test_trace_scopes_do_not_leak_between_threads_or_after_exception(self):
        from concurrent.futures import ThreadPoolExecutor
        with model_usage.model_usage_scope(trace_id='parent'):
            with ThreadPoolExecutor(max_workers=1) as executor:
                self.assertIsNone(executor.submit(model_usage._usage_context.get).result())
            with self.assertRaises(ValueError):
                with model_usage.model_usage_scope(trace_id='child'):
                    raise ValueError('test')
            self.assertEqual(model_usage._usage_context.get()['trace_id'], 'parent')
        self.assertIsNone(model_usage._usage_context.get())
