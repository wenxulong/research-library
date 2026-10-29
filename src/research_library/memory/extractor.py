"""从已经保存的调查结果中提取记忆候选。"""

import json

from research_library.core.llm_service import generate_text
from research_library.core.structured_output import generate_validated_json
from research_library.memory.prompts import MEMORY_EXTRACTION_SYSTEM_PROMPT
from research_library.memory.schemas import MemoryExtractionResponse
from research_library.schemas import SavedReportClaimInvestigationResponse


class MemoryExtractor:
    """调用模型提取记忆，不直接写数据库。"""

    def _parse(
        self,
        text: str,
    ) -> MemoryExtractionResponse:
        """检查模型返回的JSON是否符合候选结构。"""

        try:
            return MemoryExtractionResponse.model_validate_json(
                text,
                strict=True,
            )

        except ValueError as error:
            raise RuntimeError(
                f"记忆提取格式错误：{error}"
            ) from error

    def extract(
        self,
        investigation: SavedReportClaimInvestigationResponse,
    ) -> MemoryExtractionResponse:
        """根据保存后的调查、工具过程和复审结果提取候选。"""

        result = investigation.result

        research_steps = []

        for step in result.steps:
            if step.get("tool_name") not in (
                "search_paper_evidence",
                "read_neighbor_chunks",
            ):
                continue

            observation = step.get("observation", {})

            research_steps.append(
                {
                    "tool_name": step.get("tool_name"),
                    "arguments": step.get("arguments"),
                    "ok": observation.get("ok"),
                    "error": observation.get("error"),
                    "new_evidence_count": observation.get(
                        "new_evidence_count",
                    ),
                }
            )

        user_prompt = json.dumps(
            {
                "investigation_id": investigation.id,
                "task_id": result.task_id,
                "claim_id": result.claim_id,
                "instruction": result.instruction,
                "execution_status": result.status,
                "investigation_summary": result.summary,
                "previous_review": result.previous_review.model_dump(
                    mode="json",
                ),
                "final_review": result.review.model_dump(
                    mode="json",
                ),
                "research_steps": research_steps,
                "output_schema": (
                    MemoryExtractionResponse.model_json_schema()
                ),
            },
            ensure_ascii=False,
        )

        return generate_validated_json(
            generate=generate_text,
            parse=self._parse,
            system_prompt=MEMORY_EXTRACTION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )