"""记录模型请求用量；不保存提示词、回答、密钥或连接信息。"""
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from time import perf_counter

from research_library.core.config import settings


# 按请求隔离标签；退出作用域时恢复，避免并发调查互相串号。
_usage_context = ContextVar("model_usage_context", default=None)


@contextmanager
def model_usage_scope(**fields):
    token = _usage_context.set({**(_usage_context.get() or {}), **fields})
    try:
        yield
    finally:
        _usage_context.reset(token)


def measure_context(messages: list[dict], tools: list[dict] | None = None) -> dict:
    """只统计结构和字符数；字符数不冒充token数。"""
    return {
        "message_count": len(messages),
        "message_chars": len(json.dumps(messages, ensure_ascii=False)),
        "tool_schema_chars": len(json.dumps(tools, ensure_ascii=False)) if tools else 0,
        "tool_result_count": sum(message.get("role") == "tool" for message in messages),
    }


def write_usage_record(record: dict) -> None:
    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        with (settings.log_dir / "llm_usage.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        logging.getLogger(__name__).warning("模型用量日志写入失败")


def record_investigation_saved(investigation_id: int) -> None:
    # 数据库编号在查证完成后才生成，以trace_id将前面的调用关联到该编号。
    write_usage_record({
        **(_usage_context.get() or {}),
        "event": "investigation_saved",
        "time": datetime.now(timezone.utc).isoformat(),
        "investigation_id": investigation_id,
    })


def record_model_usage(*, response=None, started_at: float, call_type: str, error=None, context_stats: dict | None = None) -> None:
    """追加一条SDK调用记录；未知用量保留null，日志失败不影响模型结果。"""
    usage = getattr(response, "usage", None)
    choices = getattr(response, "choices", None)
    finish_reason = getattr(choices[0], "finish_reason", None) if choices else None
    model = getattr(response, "model", None)
    response_id = getattr(response, "id", None)

    def token_count(name):
        value = getattr(usage, name, None)
        return value if type(value) is int else None

    record = {
        **(_usage_context.get() or {}),
        **(context_stats or {}),
        "event": "model_call",
        "time": datetime.now(timezone.utc).isoformat(),
        "response_id": response_id if isinstance(response_id, str) else None,
        "model": model if isinstance(model, str) else settings.deepseek_model,
        "call_type": call_type,
        # API返回不代表JSON校验或业务流程成功。
        "status": "request_failed" if error is not None else "api_returned",
        "elapsed_seconds": round(perf_counter() - started_at, 3),
        "input_tokens": token_count("prompt_tokens"),
        "output_tokens": token_count("completion_tokens"),
        "total_tokens": token_count("total_tokens"),
        "finish_reason": finish_reason if isinstance(finish_reason, str) else None,
        "error_type": type(error).__name__ if error is not None else None,
    }
    write_usage_record(record)


def record_memory_extraction(
    status: str,
    *,
    started_at: float,
    memory_ids: list[int] | None = None,
    created_count: int | None = None,
    reused_count: int | None = None,
    error: Exception | None = None,
) -> None:
    """只记录提取状态、编号和数量；失败时未知写入数量保持null。"""
    write_usage_record({
        **(_usage_context.get() or {}),
        "event": "memory_extraction",
        "time": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "elapsed_seconds": round(perf_counter() - started_at, 3),
        "memory_ids": memory_ids,
        "created_count": created_count,
        "reused_count": reused_count,
        "error_type": type(error).__name__ if error is not None else None,
    })
