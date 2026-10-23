from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
import research_library.services.report_claim_service as report_claim_service
import research_library.services.report_claim_investigation as report_claim_investigation
import research_library.repositories.report_claim_investigation_crud as report_claim_investigation_crud
import research_library.services.report_claim_adoption as report_claim_adoption
from research_library.db.database import get_db
from research_library.schemas import ReportClaimContextResponse, ReportClaimInvestigationRequest, SavedReportClaimInvestigationResponse, ReportClaimAdoptionResponse
from fastapi import Query
import research_library.repositories.report_job_crud as report_job_crud

from fastapi import APIRouter

router = APIRouter()

@router.get(
    "/report-jobs/{job_id}/claims/{claim_id}",
    response_model=ReportClaimContextResponse,
)
def read_report_claim_context(
    job_id: int,
    claim_id: str,
    db: Session = Depends(get_db),
):
    """只读查询最终陈述、审校意见与原文，不调用模型。"""
    try:
        return report_claim_service.get_report_claim_context(db, job_id, claim_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/report-jobs/{job_id}/claims/{claim_id}/investigations",
    response_model=SavedReportClaimInvestigationResponse,
    status_code=201,
)
def create_report_claim_investigation(
    job_id: int, claim_id: str, request: ReportClaimInvestigationRequest,
    db: Session = Depends(get_db),
):
    """用户发起查证，保存独立候选记录，不采用、不覆盖报告。"""
    if not request.instruction.strip():
        raise HTTPException(status_code=422, detail="查证要求不能为空")
    try:
        return report_claim_investigation.create_saved_investigation(
            db=db, job_id=job_id, claim_id=claim_id, instruction=request.instruction,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.get(
    "/report-jobs/{job_id}/claims/{claim_id}/investigations",
    response_model=list[SavedReportClaimInvestigationResponse],
)
def read_report_claim_investigations(
    job_id: int, claim_id: str, db: Session = Depends(get_db),
    limit: int = Query(default=10, ge=1, le=20),
):
    """恢复本陈述的已保存候选，不依赖原文仍存在，不调用模型。"""
    if report_job_crud.get_report_job(db, job_id) is None:
        raise HTTPException(status_code=404, detail="没有找到这个报告作业")
    return report_claim_investigation_crud.list_investigations(db, job_id, claim_id, limit)


@router.get(
    "/report-claim-investigations/{investigation_id}",
    response_model=SavedReportClaimInvestigationResponse,
)
def read_report_claim_investigation(investigation_id: int, db: Session = Depends(get_db)):
    """按编号读取候选记录，不重新调用模型。"""
    try:
        return report_claim_investigation_crud.get_investigation(db, investigation_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post(
    "/report-claim-investigations/{investigation_id}/adopt",
    response_model=ReportClaimAdoptionResponse,
)
def adopt_report_claim_investigation(investigation_id: int, db: Session = Depends(get_db)):
    """仅采用数据库中的候选记录，不接收前端自填的证据与结论。"""
    try:
        return report_claim_adoption.adopt_investigation(db, investigation_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
