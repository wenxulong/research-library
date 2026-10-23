import json

from pydantic import ValidationError

from research_library.core.config import PROJECT_ROOT
from research_library.core.llm_service import generate_text
from research_library.agents.paper_analysis_agent import remove_json_code_block
import research_library.agents.paper_draft_agent as paper_draft_agent

from research_library.schemas import PaperDraftExpandRequest, PaperDraftExpandResponse, PaperDraftMaterials, PaperDraftResponse, PaperDraftSkillDecision, PaperDraftSkillResponse

from research_library.core.structured_output import generate_validated_json

SKILL_NAME = "paper-section-expand"

SKILL_PATH = (
    PROJECT_ROOT
    / "skills"
    / SKILL_NAME
    / "SKILL.md"
)

def parse_skill_decision(
    text: str,
) -> PaperDraftSkillDecision:
    """解析模型选择的动作，并检查扩写参数。"""

    cleaned = remove_json_code_block(text)

    try:
        decision = PaperDraftSkillDecision.model_validate_json(
            cleaned,
            strict=True,
        )
    except ValidationError as error:
        raise RuntimeError(
            "Skill决策的JSON格式或字段不正确"
        ) from error

    if decision.action == "expand_section":
        if (
            decision.section is None
            or not decision.instruction.strip()
        ):
            raise RuntimeError(
                "执行章节扩写时，必须提供章节和扩写要求"
            )

    return decision

def run_paper_writing_skill(
    draft_id: int,
    draft: PaperDraftResponse,
    materials: PaperDraftMaterials,
    instruction: str,
) -> PaperDraftSkillResponse:
    """加载章节扩写Skill，识别要求并调用现有工具。"""

    skill_instructions = SKILL_PATH.read_text(encoding="utf-8")

    system_prompt = (
            "你负责根据Skill说明选择下一步动作。"
            "本次只处理输入中指定的当前初稿。"
            "识别用户希望扩写的章节，整理具体要求。"
            "请求不明确或超出Skill范围时，选择ask_user。"
            "此阶段不要生成论文正文。"
            "只返回符合指定结构的JSON。\n\n"
            + skill_instructions
    )

    user_prompt = json.dumps(
        {
            "current_draft_id": draft_id,
            "current_title": draft.content.title,
            "user_instruction": instruction,
            "output_schema": (
                PaperDraftSkillDecision.model_json_schema()
            ),
        },
        ensure_ascii=False,
    )

    decision = generate_validated_json(
        generate=generate_text,
        parse=parse_skill_decision,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )

    if decision.action == "ask_user":
        return PaperDraftSkillResponse(
            skill_name=SKILL_NAME,
            status="needs_clarification",
            message=(
                    decision.message
                    or "请明确需要扩写的章节和具体要求。"
            ),
        )

    expand_request = PaperDraftExpandRequest(
        section=decision.section,
        instruction=decision.instruction,
    )

    section_content = (
        paper_draft_agent.expand_paper_draft_section(
            draft=draft,
            materials=materials,
            request=expand_request,
            skill_instructions=skill_instructions,
        )
    )

    return PaperDraftSkillResponse(
        skill_name=SKILL_NAME,
        status="suggested",
        message="扩写建议已生成，请确认后再保存。",
        instruction=decision.instruction,
        suggestion=PaperDraftExpandResponse(
            draft_id=draft_id,
            section=expand_request.section,
            paragraphs=section_content.paragraphs,
        ),
    )
