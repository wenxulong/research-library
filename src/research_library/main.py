from fastapi import HTTPException
import research_library.repositories.task_crud as task_crud
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from research_library.services.paper_operation_guard import PaperOperationBusyError

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from research_library.core.config import PROJECT_ROOT
from research_library.api.health import router as health_router

app = FastAPI(title="个人科研资料库")
app.include_router(health_router)
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
FRONTEND_FILE = FRONTEND_DIST / "index.html"
app.mount("/app/assets", StaticFiles(directory=FRONTEND_DIST / "assets", check_dir=False), name="frontend-assets")

@app.exception_handler(task_crud.TaskStateConflictError)
@app.exception_handler(PaperOperationBusyError)
async def handle_paper_operation_busy(request, error):
    """论文操作冲突时返回409，并保留可查询的作业编号。"""
    return JSONResponse(status_code=409, content={"detail": str(error)})


@app.get("/")
def read_root():
    """返回服务运行状态。"""
    return {"message": "个人科研资料库 API 正在运行"}


@app.get("/app", include_in_schema=False)
@app.get("/app/", include_in_schema=False)
def read_frontend():
    """返回Vue构建页面；不把未经编译的源码交给浏览器。"""
    if not FRONTEND_FILE.is_file():
        raise HTTPException(status_code=503, detail="前端尚未构建，请在frontend目录执行npm run build")
    return FileResponse(FRONTEND_FILE, headers={"Cache-Control": "no-cache"})


from research_library.api.papers import router as papers_router
app.include_router(papers_router)

from research_library.api.tasks import router as tasks_router
app.include_router(tasks_router)

from research_library.api.reports import router as reports_router
app.include_router(reports_router)

from research_library.api.investigations import router as investigations_router
app.include_router(investigations_router)

from research_library.api.planning import router as planning_router
app.include_router(planning_router)

from research_library.api.experiments import router as experiments_router
app.include_router(experiments_router)

from research_library.api.drafts import router as drafts_router
app.include_router(drafts_router)
