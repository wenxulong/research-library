"""模型 JSON 输出的校验与有限重试，网络错误交由 SDK 处理。"""

from collections.abc import Callable
from typing import TypeVar

from research_library.core.llm_service import ModelOutputError

T = TypeVar("T")


def generate_validated_json(
    generate: Callable[..., str],
    parse: Callable[[str], T],
    system_prompt: str,
    user_prompt: str,
) -> T:
    """最多请求两次；错误反馈只用于修正格式、字段和引用。"""
    feedback = ""
    for attempt in range(2):
        try:
            text = generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt + feedback,
                json_mode=True,
            )
        except ModelOutputError as error:
            validation_error = str(error)
        else:
            try:
                return parse(text)
            except RuntimeError as error:
                validation_error = str(error)
        if attempt == 1:
            raise RuntimeError(f"JSON输出校验失败（已尝试2次）：{validation_error}")
        feedback = (
            "\n\n上一次输出未通过程序校验：" + validation_error
            + "。请重新返回完整合法JSON，遵守字段和资料引用要求，不编造。"
            + "缩短陈述，避免输出被截断；不要输出代码块或额外文字。"
        )
    raise RuntimeError("未取得有效JSON输出")
