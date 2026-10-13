"""区分API存活检查和完整科研流程的依赖检查。"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse
import health_service

router = APIRouter(prefix="/health", tags=["运行状态"])


@router.get("/live")
def health_live():
    """只说明API可以响应，不连接数据库、队列或模型。"""
    return {"status": "ok"}


@router.get("/ready")
def health_ready():
    """依赖全部确认可用返回200；异常或未确认返回503和具体状态。"""
    report = health_service.build_health_report()
    return JSONResponse(status_code=200 if report["status"] == "ok" else 503, content=report)
