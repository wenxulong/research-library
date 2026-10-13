"""配置共用Redis队列的Celery应用与两类后台任务。"""
from celery import Celery
from config import settings, require_value

redis_url = require_value("REDIS_URL", settings.redis_url)

celery_app = Celery(
    "research_library",
    broker=redis_url,
    include=[
        "report_worker",
        "paper_preparation_worker",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_default_queue=settings.celery_queue,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)