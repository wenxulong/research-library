"""有超时的依赖检查；仅返回状态，不返回连接串、密钥或异常原文。"""
from concurrent.futures import ThreadPoolExecutor
import json
from urllib.request import urlopen

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from celery import Celery
from redis import Redis
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from research_library.core.config import settings, get_deepseek_api_key, require_value
from research_library.db.db_migrations import migration_config

REQUIRED_TASKS = {"papers.run_preparation_job", "reports.run_report_job"}


def check_database() -> dict:
    """使用短超时的独立连接检查可用性及迁移版本。"""
    url = require_value("DATABASE_URL", settings.database_url)
    options = {}
    if make_url(url).get_backend_name() == "mysql":
        options = {"connect_timeout": max(1, int(settings.health_timeout)),
                   "read_timeout": settings.health_timeout,
                   "write_timeout": settings.health_timeout}
    engine = create_engine(url, poolclass=NullPool, connect_args=options)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            current = set(MigrationContext.configure(connection).get_current_heads())
            expected = set(ScriptDirectory.from_config(migration_config()).get_heads())
        if current != expected:
            return {"status": "error", "message": "数据库可连接，但迁移版本不匹配，请运行python manage.py init"}
        return {"status": "ok", "message": "数据库可用，迁移版本正确"}
    finally:
        engine.dispose()


def check_redis() -> dict:
    """不读取业务消息，只执行PING。"""
    with Redis.from_url(require_value("REDIS_URL", settings.redis_url),
                        socket_connect_timeout=settings.health_timeout,
                        socket_timeout=settings.health_timeout) as client:
        client.ping()
    return {"status": "ok", "message": "Redis可用"}


def check_ollama() -> dict:
    """检查服务及本地模型清单，不调用生成或向量接口。"""
    with urlopen(settings.ollama_base_url + "/api/tags", timeout=settings.health_timeout) as response:
        payload = json.load(response)
    names = {item["name"].removesuffix(":latest") for item in payload["models"]}
    if settings.embedding_model.removesuffix(":latest") not in names:
        return {"status": "error", "message": "Ollama可连接，但指定向量模型未安装"}
    return {"status": "ok", "message": "Ollama可用，向量模型已安装"}


def check_worker() -> dict:
    """核对同一个Worker同时注册两类任务并消费配置队列。"""
    app = Celery("research_health", broker=settings.redis_url, set_as_current=False)
    app.conf.update(broker_connection_timeout=settings.health_timeout,
                    broker_connection_max_retries=0, task_publish_retry=False,
                    broker_transport_options={"socket_connect_timeout": settings.health_timeout,
                                              "socket_timeout": settings.health_timeout})
    try:
        inspector = app.control.inspect(timeout=settings.health_timeout)
        registered = inspector.registered() or {}
        if not registered:
            return {"status": "unknown", "message": "未收到Worker响应；可能离线或solo正在执行任务"}
        queues = inspector.active_queues() or {}
        valid = [name for name, tasks in registered.items()
                 if REQUIRED_TASKS.issubset(set(tasks))
                 and any(queue["name"] == settings.celery_queue for queue in queues.get(name, []))]
        if not valid:
            return {"status": "unknown", "message": "未确认Worker同时注册两类任务并消费正确队列"}
        return {"status": "ok", "message": "Worker任务与队列正确", "workers": sorted(valid)}
    finally:
        app.close()


def safe_check(function) -> dict:
    """错误只显示类型，避免异常原文中的密码、主机或响应内容泄露。"""
    try:
        return function()
    except Exception as error:
        return {"status": "error", "message": "检查失败", "error_type": type(error).__name__}


def build_health_report() -> dict:
    """并行检查独立依赖，Redis失败时跳过Worker远程检查。"""
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {name: pool.submit(safe_check, function)
                   for name, function in {'database': check_database,
                                          "redis": check_redis,
                                          "ollama": check_ollama}.items()}
        checks = {name: future.result() for name, future in futures.items()}
    checks["worker"] = (safe_check(check_worker) if checks["redis"]["status"] == "ok"
                        else {"status": "unknown", "message": "Redis不可用，跳过Worker检查"})
    return {"status": "ok" if all(item["status"] == "ok" for item in checks.values()) else "degraded",
            "checks": checks,
            "capabilities": {"deepseek_configured": bool(get_deepseek_api_key(required=False))}}
