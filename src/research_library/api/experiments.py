from fastapi import Depends, HTTPException, File, UploadFile
from sqlalchemy.orm import Session
from research_library.services.research_report_export import build_research_report_zip
import research_library.services.experiment_import_service as experiment_import_service
import research_library.services.experiment_statistics_service as experiment_statistics_service
import research_library.services.experiment_chart_service as experiment_chart_service
import research_library.agents.experiment_analysis_agent as experiment_analysis_agent
from research_library.db.model import ExperimentImport
from research_library.db.model import ResearchQuestionSet
from research_library.db.database import get_db
from research_library.schemas import ExperimentImportResponse, ExperimentStatisticsResponse, ExperimentAnalysisRequest, ExperimentAnalysisResponse
from fastapi import Query
from fastapi.responses import Response

from fastapi import APIRouter

router = APIRouter()

@router.post(
    "/research-question-sets/{set_id}/experiment-imports",
    response_model=ExperimentImportResponse,
    status_code=201,
)
async def import_experiment_results_endpoint(
    set_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上传CSV，保存实验结果和对应计划。"""

    record = db.get(ResearchQuestionSet, set_id)

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这组候选研究问题",
        )

    try:
        content = await file.read()

        raw_csv = content.decode("utf-8-sig")

        return experiment_import_service.import_experiment_results(
            db=db,
            record=record,
            filename=file.filename or "results.csv",
            raw_csv=raw_csv,
        )

    except UnicodeDecodeError as error:
        raise HTTPException(
            status_code=400,
            detail="请上传UTF-8编码的CSV文件",
        ) from error

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    finally:
        await file.close()


@router.get(
    "/experiment-imports/{import_id}",
    response_model=ExperimentImportResponse,
)
def read_experiment_import(
    import_id: int,
    db: Session = Depends(get_db),
):
    """读取一批已导入的实验结果。"""

    imported = db.get(ExperimentImport, import_id)

    if imported is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这次实验结果导入",
        )

    return imported


@router.get(
    "/experiment-imports/{import_id}/statistics",
    response_model=ExperimentStatisticsResponse,
)
def read_experiment_statistics(
    import_id: int,
    metric: str = Query(min_length=1),
    db: Session = Depends(get_db),
):
    """读取一次导入中指定指标的分组统计。"""

    imported = db.get(ExperimentImport, import_id)

    if imported is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这次实验结果导入",
        )

    try:
        return experiment_statistics_service.summarize_experiment_results(
            imported=imported,
            metric=metric,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error


@router.get(
    "/experiment-imports/{import_id}/chart",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def download_experiment_chart(
    import_id: int,
    metric: str = Query(min_length=1),
    dataset: str = Query(min_length=1),
    scenario: str = Query(min_length=1),
    db: Session = Depends(get_db),
):
    """下载指定指标、数据集和场景的方法对比图。"""

    imported = db.get(ExperimentImport, import_id)

    if imported is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这次实验结果导入",
        )

    try:
        statistics = (
            experiment_statistics_service
            .summarize_experiment_results(
                imported=imported,
                metric=metric,
            )
        )

        image_bytes = experiment_chart_service.build_experiment_chart(
            statistics=statistics,
            dataset=dataset,
            scenario=scenario,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={
            "Content-Disposition": (
                f'attachment; filename="experiment-{import_id}.png"'
            ),
        },
    )


@router.post(
    "/experiment-imports/{import_id}/analysis",
    response_model=ExperimentAnalysisResponse,
)
def analyze_experiment_results(
    import_id: int,
    request: ExperimentAnalysisRequest,
    db: Session = Depends(get_db),
):
    """根据一次实验导入记录生成多指标分析。"""
    imported = db.get(ExperimentImport, import_id)
    if imported is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这次实验结果导入",
        )

    try:
        return experiment_analysis_agent.generate_experiment_analysis(
            imported=imported,
            request=request,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        ) from error


@router.post(
    "/experiment-imports/{import_id}/research-report/export",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/zip": {},
            },
        },
    },
)
def export_research_report(
    import_id: int,
    request: ExperimentAnalysisRequest,
    db: Session = Depends(get_db),
):
    """生成实验分析并导出完整研究报告压缩包。"""
    imported = db.get(ExperimentImport, import_id)
    if imported is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这次实验结果导入",
        )

    try:
        analysis = experiment_analysis_agent.generate_experiment_analysis(
            imported=imported,
            request=request,
        )

        archive_bytes = build_research_report_zip(
            imported=imported,
            analysis=analysis,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        ) from error

    return Response(
        content=archive_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="research-report-{import_id}.zip"'
            ),
        },
    )
