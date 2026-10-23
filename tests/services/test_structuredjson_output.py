"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestStructuredJSONOutput(unittest.TestCase):
    def test_invalid_json_retries_once_with_feedback_and_json_mode(self):
        from research_library.core.structured_output import generate_validated_json
        def parse(text):
            try:
                return json.loads(text)
            except ValueError as error:
                raise RuntimeError("JSON语法错误") from error
        generate = Mock(side_effect=["bad", '{"ok": true}'])
        result = generate_validated_json(generate, parse, "返回JSON", "问题")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(generate.call_count, 2)
        self.assertTrue(all(call.kwargs["json_mode"] for call in generate.call_args_list))
        self.assertIn("JSON语法错误", generate.call_args_list[1].kwargs["user_prompt"])

    def test_validation_failure_stops_after_two_attempts(self):
        from research_library.core.structured_output import generate_validated_json
        generate = Mock(return_value="invalid")
        with self.assertRaisesRegex(RuntimeError, "已尝试2次"):
            generate_validated_json(generate, Mock(side_effect=RuntimeError("字段错误")), "JSON", "问题")
        self.assertEqual(generate.call_count, 2)

    def test_network_error_is_not_retried_by_validation_layer(self):
        from research_library.core.structured_output import generate_validated_json
        generate = Mock(side_effect=RuntimeError("网络失败"))
        with self.assertRaisesRegex(RuntimeError, "网络失败"):
            generate_validated_json(generate, Mock(), "JSON", "问题")
        generate.assert_called_once()

    def test_empty_or_truncated_output_retries(self):
        from research_library.core.structured_output import generate_validated_json
        from research_library.core.llm_service import ModelOutputError
        for message in ("回答为空", "回答被截断"):
            generate = Mock(side_effect=[ModelOutputError(message), "{}"])
            self.assertEqual(generate_validated_json(generate, json.loads, "JSON", "问题"), {})
            self.assertEqual(generate.call_count, 2)

    def test_sdk_json_mode_sets_response_format_and_limits_tokens(self):
        import research_library.core.llm_service as llm_service
        sdk = Mock()
        sdk.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content='{"ok": true}'),
        )])
        with patch('research_library.core.llm_service.OpenAI', return_value=sdk), patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-only"}):
            self.assertEqual(llm_service.generate_text("返回JSON", "问题", json_mode=True), '{"ok": true}')
            options = sdk.chat.completions.create.call_args.kwargs
            self.assertEqual(options["response_format"], {"type": "json_object"})
            self.assertEqual(options["max_tokens"], 8192)
            llm_service.generate_text("回答问题", "问题")
            self.assertNotIn("response_format", sdk.chat.completions.create.call_args.kwargs)

    def test_sdk_empty_and_truncated_outputs_are_retryable_errors(self):
        import research_library.core.llm_service as llm_service
        sdk = Mock()
        with patch('research_library.core.llm_service.OpenAI', return_value=sdk), patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-only"}):
            for reason, content in (("length", '{"truncated":'), ("stop", "")):
                sdk.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
                    finish_reason=reason, message=SimpleNamespace(content=content),
                )])
                with self.assertRaises(llm_service.ModelOutputError):
                    llm_service.generate_text("JSON", "问题", json_mode=True)

    def test_review_retries_bad_json_then_checks_citations(self):
        claim = ReviewClaim("experiments", "成本下降。[资料3]", [3], [ReviewEvidence(3, "论文", 1, 1, "成本下降。")])
        answer = json.dumps(dict(verdict="supported", reason="原文支持", source_numbers=[3]))
        with patch('research_library.agents.review_agent.generate_text', side_effect=["bad", answer]) as generate:
            result = review_agent.review_claim(claim)
        self.assertEqual(result.verdict, "supported")
        self.assertEqual(generate.call_count, 2)
