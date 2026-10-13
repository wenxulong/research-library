import re

from llm_service import generate_text


def contains_chinese(text: str) -> bool:
    """判断文本中是否包含中文字符。"""

    return re.search(
        r"[\u4e00-\u9fff]",
        text,
    ) is not None


def rewrite_query_for_search(question: str) -> str:
    """将中文科研问题扩展成中英文组合检索问题。"""

    question = question.strip()

    if not question:
        raise ValueError("问题不能为空")

    if not contains_chinese(question):
        return question

    system_prompt = (
        "你是科研检索系统中的查询改写Agent。"
        "请把用户的中文科研问题改写成准确、简洁的"
        "英文学术检索问题。"
        "必须保留算法名称、模型名称和专业术语。"
        "只输出英文检索问题，不要解释，不要添加标题。"
    )

    user_prompt = (
        "请将下面的科研问题改写为英文检索问题：\n"
        f"{question}"
    )

    english_query = generate_text(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )

    english_query = english_query.strip()

    if not english_query:
        return question

    return (
        f"{question}\n"
        f"{english_query}"
    )