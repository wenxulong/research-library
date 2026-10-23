"""统一迁移、检查、API/Worker前台启动入口。"""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

from research_library.core.config import PROJECT_ROOT, settings


def api_log_config() -> dict:
    """API同时输出终端与日志文件，避免记录请求正文或密钥。"""
    from uvicorn.config import LOGGING_CONFIG
    config = deepcopy(LOGGING_CONFIG)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    config["formatters"]["file"] = {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}
    config["handlers"]["file"] = {"class": "logging.handlers.RotatingFileHandler",
                                  "formatter": "file", "filename": str(settings.log_dir / 'api.log'),
                                  "maxBytes": 10 * 1024 * 1024, "backupCount": 3, "encoding": "utf-8"}
    config["loggers"]["uvicorn"]["handlers"].append("file")
    config["loggers"]["uvicorn.access"]["handlers"].append("file")
    return config


def worker_command() -> list[str]:
    """Worker消费与API投递一致的队列；solo为当前Mac的运行方式。"""
    return [sys.executable, "-B", "-m", "celery", "-A", "research_library.workers.celery_app", "worker",
            "--loglevel=" + settings.log_level.upper(), "--pool=solo", "--concurrency=1",
            "--hostname=research-library@%h", "--queues=" + settings.celery_queue,
            "--logfile=" + str(settings.log_dir / "worker.log")]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="科研资料库后端维护与启动")
    parser.add_argument("command", choices=["init", "check", 'api', "worker"])
    args = parser.parse_args(argv)
    os.chdir(PROJECT_ROOT)
    if args.command == "init":
        from research_library.db.init_db import init_db
        init_db()
    elif args.command == "check":
        from research_library.services.health_service import build_health_report
        report = build_health_report()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "ok" else 1
    elif args.command == 'api':
        import uvicorn
        uvicorn.run('research_library.main:app', host=settings.api_host, port=settings.api_port,
                    log_level=settings.log_level, log_config=api_log_config())
    else:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        command = worker_command()
        # 用Worker替换当前进程，Ctrl+C/SIGTERM直接送达真实Worker。
        os.execv(sys.executable, command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
