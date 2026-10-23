"""统一读取运行配置；环境变量优先于项目根目录 .env。"""
from collections.abc import Mapping
from dataclasses import dataclass, field
import math
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def read_text(environ: Mapping[str, str], name: str, default: str = "") -> str:
    """去掉首尾空白；可选配置留空时使用默认值。"""
    return environ.get(name, default).strip() or default


def read_int(environ, name: str, default: int, minimum: int = 1) -> int:
    """验证整数配置，不在错误信息中输出配置值。"""
    try:
        value = int(read_text(environ, name, str(default)))
    except ValueError as error:
        raise RuntimeError(f"{name}必须是整数") from error
    if value < minimum:
        raise RuntimeError(f"{name}不能小于{minimum}")
    return value


def read_timeout(environ, name: str, default: float) -> float:
    """超时必须是有限正数，不能是负数、NaN或无穷大。"""
    try:
        value = float(read_text(environ, name, str(default)))
    except ValueError as error:
        raise RuntimeError(f"{name}必须是数字") from error
    if not math.isfinite(value) or value <= 0:
        raise RuntimeError(f"{name}必须是有限正数")
    return value


def resolve_path(value: str) -> Path:
    """相对路径始终相对项目目录，不依赖启动时所在目录。"""
    path = Path(value).expanduser()
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def require_value(name: str, value: str) -> str:
    """仅在对应功能使用时检查必需配置。"""
    if not value:
        raise RuntimeError(f"没有找到{name}，请检查项目.env或环境变量")
    return value


@dataclass(frozen=True)
class Settings:
    """一个进程内固定的非密钥配置；数据库和Redis连接不参与repr。"""
    database_url: str = field(repr=False)
    redis_url: str = field(repr=False)
    celery_queue: str
    ollama_base_url: str
    embedding_model: str
    embedding_timeout: float
    deepseek_base_url: str
    deepseek_model: str
    deepseek_timeout: float
    deepseek_max_retries: int
    deepseek_json_max_tokens: int
    upload_dir: Path
    max_upload_mb: int
    api_host: str
    api_port: int
    log_level: str
    log_dir: Path
    health_timeout: float


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """测试可传入独立字典；不改动进程环境，不连接任何服务。"""
    environ = os.environ if environ is None else environ
    port = read_int(environ, "API_PORT", 8000)
    if port > 65535:
        raise RuntimeError("API_PORT不能超过65535")
    level = read_text(environ, "LOG_LEVEL", "info").lower()
    if level not in {"debug", "info", "warning", "error", "critical"}:
        raise RuntimeError("LOG_LEVEL不是支持的日志级别")
    return Settings(
        database_url=read_text(environ, "DATABASE_URL"),
        redis_url=read_text(environ, "REDIS_URL"),
        celery_queue=read_text(environ, "CELERY_QUEUE", "report_jobs"),
        ollama_base_url=read_text(environ, "OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
        embedding_model=read_text(environ, "EMBEDDING_MODEL", "qwen3-embedding:0.6b"),
        embedding_timeout=read_timeout(environ, "EMBEDDING_TIMEOUT", 120.0),
        deepseek_base_url=read_text(environ, "DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        deepseek_model=read_text(environ, "DEEPSEEK_MODEL", "deepseek-flash"),
        deepseek_timeout=read_timeout(environ, "DEEPSEEK_TIMEOUT", 300.0),
        deepseek_max_retries=read_int(environ, "DEEPSEEK_MAX_RETRIES", 2, minimum=0),
        deepseek_json_max_tokens=read_int(environ, "DEEPSEEK_JSON_MAX_TOKENS", 8192),
        upload_dir=resolve_path(read_text(environ, "UPLOAD_DIR", "uploads/papers")),
        max_upload_mb=read_int(environ, "MAX_UPLOAD_MB", 20),
        api_host=read_text(environ, "API_HOST", "127.0.0.1"),
        api_port=port,
        log_level=level,
        log_dir=resolve_path(read_text(environ, "LOG_DIR", "logs")),
        health_timeout=read_timeout(environ, "HEALTH_TIMEOUT", 2.0),
    )


def get_deepseek_api_key(required: bool = True) -> str:
    """密钥按调用时读取，不放入Settings打印，也不阻止纯数据库工具。"""
    value = read_text(os.environ, "DEEPSEEK_API_KEY")
    return require_value("DEEPSEEK_API_KEY", value) if required else value


settings = load_settings()
