"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestComparisonReportParsing(unittest.TestCase):
    """独立测试模型返回内容的结构、编号及不同论文的引用检查。"""

    def setUp(self):
        from research_library.schemas import ComparisonSourceResponse
        self.sources = [
            ComparisonSourceResponse(source_number=number, paper_id=paper_id, paper_title=f"论文{paper_id}",
                                     chunk_id=number + 100, page_number=number, score=0.9)
            for number, paper_id in ((1, 11), (3, 22), (5, 11))
        ]
        self.data = dict(
            method_comparisons=[dict(text="A与B采用不同匹配机制。[资料1][资料3]", source_numbers=[5])],
            experiment_comparisons=[], conclusion_comparisons=[],
            evidence_notes=[dict(text="当前A片段未说明在线实验。[资料5]")],
        )

    def _parse(self, data=None, text=None, sources=None):
        import research_library.agents.comparison_agent as comparison_agent
        return comparison_agent.parse_comparison_response(
            json.dumps(self.data if data is None else data) if text is None else text,
            task_id=7, question="研究问题",
            sources=self.sources if sources is None else sources,
        )

    def _copy_data(self):
        return json.loads(json.dumps(self.data))

    def test_valid_report_uses_program_metadata_and_extracts_actual_citations(self):
        data = self._copy_data()
        data.update(task_id=999, question="模型编造的问题", paper_ids=[999], sources=[])
        report = self._parse(data=data)
        self.assertEqual(report.task_id, 7)
        self.assertEqual(report.question, "研究问题")
        self.assertEqual(report.paper_ids, [11, 22])
        self.assertEqual(report.sources, self.sources)
        self.assertEqual(report.method_comparisons[0].source_numbers, [1, 3])
        self.assertEqual(report.evidence_notes[0].source_numbers, [5])

    def test_json_code_block_is_accepted(self):
        report = self._parse(text="```json\n" + json.dumps(self.data) + "\n```")
        self.assertEqual(report.method_comparisons[0].source_numbers, [1, 3])

    def test_invalid_json_or_non_object_is_rejected(self):
        for text in ("not JSON", "[]", "null", "123"):
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                self._parse(text=text)

    def test_missing_field_or_wrong_field_types_are_rejected(self):
        for name in ("method_comparisons", "experiment_comparisons", "conclusion_comparisons", "evidence_notes"):
            for value in (None, "错误字符串", ["不是正文对象"]):
                with self.subTest(name=name, value=value):
                    data = self._copy_data()
                    data[name] = value
                    with self.assertRaises(RuntimeError):
                        self._parse(data=data)
        data = self._copy_data()
        data.pop("evidence_notes")
        with self.assertRaises(RuntimeError):
            self._parse(data=data)

    def test_completely_empty_report_is_rejected(self):
        data = {name: [] for name in self.data}
        with self.assertRaisesRegex(RuntimeError, "空报告"):
            self._parse(data=data)

    def test_empty_or_whitespace_text_is_rejected(self):
        for text in ("", " \n\t "):
            with self.subTest(text=text):
                data = self._copy_data()
                data["method_comparisons"][0]["text"] = text
                with self.assertRaises(RuntimeError):
                    self._parse(data=data)

    def test_comparison_and_notes_both_require_citations(self):
        for field in ("method_comparisons", "evidence_notes"):
            with self.subTest(field=field):
                data = self._copy_data()
                data[field][0]["text"] = "没有引用的正文"
                with self.assertRaisesRegex(RuntimeError, "必须附上资料引用"):
                    self._parse(data=data)

    def test_bad_reference_syntax_or_out_of_range_number_is_rejected(self):
        for marker in ("[资料abc]", "[资料1", "[资料0]", "[资料9]"):
            with self.subTest(marker=marker):
                data = self._copy_data()
                data["method_comparisons"][0]["text"] = "正文" + marker
                with self.assertRaisesRegex(RuntimeError, "引用不合法"):
                    self._parse(data=data)

    def test_number_inside_range_but_not_provided_is_rejected(self):
        data = self._copy_data()
        data["method_comparisons"][0]["text"] = "错误引用。[资料1][资料2]"
        with self.assertRaisesRegex(RuntimeError, "本次未提供的资料：2"):
            self._parse(data=data)

    def test_two_sources_from_same_paper_do_not_count_as_two_papers(self):
        data = self._copy_data()
        data["method_comparisons"][0]["text"] = "同一论文的两份资料。[资料1][资料5]"
        with self.assertRaisesRegex(RuntimeError, "至少两篇不同论文"):
            self._parse(data=data)

    def test_all_comparison_categories_require_two_papers(self):
        for field in ("method_comparisons", "experiment_comparisons", "conclusion_comparisons"):
            with self.subTest(field=field):
                data = {name: [] for name in self.data}
                data[field] = [dict(text="单篇描述。[资料1]")]
                with self.assertRaisesRegex(RuntimeError, "至少两篇不同论文"):
                    self._parse(data=data)

    def test_notes_only_can_reference_one_paper_and_allow_empty_comparisons(self):
        data = {name: [] for name in self.data}
        data["evidence_notes"] = [dict(text="当前片段不足以比较。[资料1]")]
        report = self._parse(data=data)
        self.assertEqual(report.method_comparisons, [])
        self.assertEqual(report.evidence_notes[0].source_numbers, [1])

    def test_missing_sources_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "没有可用于检查报告引用的资料"):
            self._parse(sources=[])

    def test_citations_are_deduplicated_in_text_order_and_text_is_trimmed(self):
        data = self._copy_data()
        data["method_comparisons"][0]["text"] = " \n对比。[资料3][资料1][资料3] \n"
        report = self._parse(data=data)
        self.assertEqual(report.method_comparisons[0].source_numbers, [3, 1])
        self.assertEqual(report.method_comparisons[0].text, "对比。[资料3][资料1][资料3]")
