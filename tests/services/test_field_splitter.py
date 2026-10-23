"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestFieldSplitter(unittest.TestCase):
    """校验拆分结构、字段、引用和原文本保留。"""

    def setUp(self):
        from research_library.schemas import AnalysisRevisionResponse, FieldCheckResponse
        self.revision=AnalysisRevisionResponse(field="limitations",original_claim="原陈述",
            revised_claim="采用阶段式切换。[资料3]实验显示成本降低。[资料5]",reason="修改",source_numbers=[3,5])
        self.category=FieldCheckResponse(verdict="mixed",reason="方法和实验混合",suggested_field=None)
        self.data=dict(reason="按主题拆分",items=[
            dict(field="methods",claim="采用阶段式切换。[资料3]"),
            dict(field="experiments",claim="实验显示成本降低。[资料5]"),
        ])

    def test_valid_split_preserves_original_and_derives_citations(self):
        data=dict(self.data,source_claim="模型改写原文",source_field="methods")
        data["items"][0]["source_numbers"]=[99]
        with patch('research_library.services.field_splitter.generate_text',return_value="```json\n"+json.dumps(data)+"\n```"):
            result=field_splitter.split_mixed_revision(self.revision,self.category,5)
        self.assertEqual(result.source_claim,self.revision.revised_claim)
        self.assertEqual(result.source_field,"limitations")
        self.assertEqual([i.source_numbers for i in result.items],[[3],[5]])

    def test_nonmixed_or_invalid_input_never_calls_model(self):
        cases=[(self.revision,self.category.model_copy(update={"verdict":"appropriate"})),
               (self.revision.model_copy(update={"revised_claim":" "}),self.category),
               (self.revision.model_copy(update={"source_numbers":[3]}),self.category)]
        with patch('research_library.services.field_splitter.generate_text') as generate:
            for revision,category in cases:
                with self.subTest(revision=revision):
                    with self.assertRaises(ValueError):
                        field_splitter.split_mixed_revision(revision,category,5)
        generate.assert_not_called()

    def test_reject_invalid_structure_or_item_count(self):
        cases=["not JSON","[]","{}",json.dumps(dict(self.data,items=self.data["items"][:1])),
               json.dumps(dict(self.data,items=self.data["items"]*5))]
        for answer in cases:
            with self.subTest(answer=answer),patch('research_library.services.field_splitter.generate_text',return_value=answer):
                with self.assertRaises(RuntimeError):
                    field_splitter.split_mixed_revision(self.revision,self.category,5)

    def test_reject_blank_claim_reason_and_invalid_field(self):
        cases=[dict(self.data,reason=" "),dict(self.data,items=[dict(field="methods",claim=" "),self.data["items"][1]]),
               dict(self.data,items=[dict(field="unknown",claim="正文"),self.data["items"][1]])]
        for data in cases:
            with self.subTest(data=data),patch('research_library.services.field_splitter.generate_text',return_value=json.dumps(data)):
                with self.assertRaises(RuntimeError):
                    field_splitter.split_mixed_revision(self.revision,self.category,5)

    def test_reject_new_out_of_range_or_unclosed_citations(self):
        for citation in ("[资料1]","[资料6]","[资料3"):
            data=dict(self.data,items=[dict(field="methods",claim="正文"+citation),self.data["items"][1]])
            with self.subTest(citation=citation),patch('research_library.services.field_splitter.generate_text',return_value=json.dumps(data)):
                with self.assertRaises(RuntimeError):
                    field_splitter.split_mixed_revision(self.revision,self.category,5)

    def test_reject_split_that_stays_in_one_field(self):
        data=dict(self.data,items=[dict(field="methods",claim="方法[资料3]"),dict(field="methods",claim="另一方法[资料5]")])
        with patch('research_library.services.field_splitter.generate_text',return_value=json.dumps(data)):
            with self.assertRaisesRegex(RuntimeError,"两个不同字段"):
                field_splitter.split_mixed_revision(self.revision,self.category,5)

    def test_prompt_uses_mixed_text_and_preserves_uncertainty_rules(self):
        with patch('research_library.services.field_splitter.generate_text',return_value=json.dumps(self.data)) as generate:
            field_splitter.split_mixed_revision(self.revision,self.category,5)
        self.assertIn(self.revision.revised_claim,generate.call_args.kwargs["user_prompt"])
        self.assertIn(self.category.reason,generate.call_args.kwargs["user_prompt"])
        self.assertIn("保留范围、限定条件和不确定性",generate.call_args.kwargs["system_prompt"])


    def test_reject_added_numbers_and_missing_citations(self):
        cases=[dict(self.data,items=[self.data["items"][0],dict(field="experiments",claim="成本减少90.91%。[资料5]")]),
               dict(self.data,items=[self.data["items"][0],dict(field="limitations",claim="当前原文不足。")])]
        for data in cases:
            with self.subTest(data=data),patch('research_library.services.field_splitter.generate_text',return_value=json.dumps(data)):
                with self.assertRaises(RuntimeError):
                    field_splitter.split_mixed_revision(self.revision,self.category,5)

    def test_split_retries_once_then_accepts_corrected_output(self):
        with patch('research_library.services.field_splitter.generate_text',side_effect=["invalid JSON",json.dumps(self.data)]) as generate:
            result=field_splitter.split_mixed_revision(self.revision,self.category,5)
        self.assertEqual(generate.call_count,2)
        self.assertEqual(len(result.items),2)
        self.assertIn("上次输出未通过检查",generate.call_args.kwargs["user_prompt"])

    def test_split_retry_is_bounded(self):
        with patch('research_library.services.field_splitter.generate_text',return_value="invalid JSON") as generate:
            with self.assertRaises(RuntimeError):
                field_splitter.split_mixed_revision(self.revision,self.category,5)
        self.assertEqual(generate.call_count,2)

    def test_original_numeric_values_and_citation_ids_are_allowed(self):
        revision=self.revision.model_copy(update={"revised_claim":"采用阶段式切换。[资料3]成本下降20%。[资料5]"})
        data=dict(self.data,items=[self.data["items"][0],dict(field="experiments",claim="成本下降20%。[资料5]")])
        with patch('research_library.services.field_splitter.generate_text',return_value=json.dumps(data)):
            result=field_splitter.split_mixed_revision(revision,self.category,5)
        self.assertIn("20%",result.items[1].claim)
