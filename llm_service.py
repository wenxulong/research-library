from config import settings, get_deepseek_api_key
from openai import (
    APIConnectionError,
    APIStatusError,
    OpenAI,
)


DEEPSEEK_BASE_URL = settings.deepseek_base_url
DEEPSEEK_MODEL = settings.deepseek_model


class ModelOutputError(RuntimeError):
    """模型回答为空或被截断，可以有限重试。"""


def generate_text(
    system_prompt: str,
    user_prompt: str,
    json_mode: bool = False,
) -> str:
    system_prompt = system_prompt.strip()
    user_prompt = user_prompt.strip()

    if not system_prompt:
        raise ValueError("系统提示词不能为空")

    if not user_prompt:
        raise ValueError("用户提示词不能为空")

    api_key = get_deepseek_api_key()

    client = OpenAI(
        api_key=api_key,
        base_url=DEEPSEEK_BASE_URL,
        timeout=settings.deepseek_timeout,
        max_retries=settings.deepseek_max_retries,
    )

    options = {}
    if json_mode:
        options = {"response_format": {"type": "json_object"}, "max_tokens": settings.deepseek_json_max_tokens}

    try:
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            stream=False,
            **options,
        )

    except APIConnectionError as error:
        raise RuntimeError(
            "无法连接DeepSeek API，请检查网络"
        ) from error

    except APIStatusError as error:
        raise RuntimeError(
            f"DeepSeek API请求失败，状态码：{error.status_code}"
        ) from error

    if not response.choices:
        raise RuntimeError("DeepSeek API没有返回回答")

    if response.choices[0].finish_reason == "length":
        raise ModelOutputError("DeepSeek回答被长度限制截断，请缩短输出")

    answer = response.choices[0].message.content

    if not answer:
        raise ModelOutputError("DeepSeek返回的回答为空")

    return answer.strip()


if __name__ == "__main__":
    answer = generate_text(
        system_prompt=(
            "你是一名科研助手。"
            "请使用准确、清晰、简洁的中文回答问题。"
        ),
        user_prompt="请简单解释什么是RAG。",
    )

    print(answer)