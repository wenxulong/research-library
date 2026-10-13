"""统一检查和提取分析、审校使用的资料编号。"""

import re


def extract_source_numbers(text: str, source_count: int) -> list[int]:
    """检查每个引用开头，拒绝格式错误，并按出现顺序去重。"""
    numbers: list[int] = []

    for start in re.finditer(r"\[资料", text):
        match = re.match(r"\[资料\s*([0-9]+)\s*\]", text[start.start():])
        if match is None:
            fragment = text[start.start():].split("\n", 1)[0][:60]
            raise ValueError(f"引用格式错误：{fragment}")

        number = int(match.group(1))
        if not 1 <= number <= source_count:
            raise ValueError(
                f"引用不存在：{match.group(0)}，可用资料编号为1到{source_count}"
            )
        if number not in numbers:
            numbers.append(number)

    return numbers
