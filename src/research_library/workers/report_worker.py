from sqlalchemy import func

from research_library.workers.celery_app import celery_app
from research_library.db.database import SessionLocal
from research_library.repositories.report_job_crud import claim_report_job, get_report_job
from research_library.schemas import ResearchTaskAnalysisResponse

from research_library.agents.comparison_agent import generate_comparison_report
from research_library.services.comparison_evidence_workflow import process_comparison_evidence
from research_library.services.comparison_report_builder import assemble_comparison_report

@celery_app.task(name="reports.run_report_job")
def run_report_job(job_id: int) -> None:
    """领取作业，执行报告流程，将结果或错误保存到MySQL。"""

    with SessionLocal() as db:
        job = claim_report_job(db, job_id)

        if job is None:
            return

        try:
            analysis = ResearchTaskAnalysisResponse.model_validate(
                job.analysis_snapshot
            )

            if analysis.task_id != job.task_id:
                raise ValueError("作业输入与研究任务编号不一致")

            report = generate_comparison_report(
                db=db,
                analysis=analysis,
            )

            job.stage = "processing"
            db.commit()

            processing_result = process_comparison_evidence(
                db=db,
                analysis=analysis,
                report=report,
            )

            job.stage = "assembling"
            db.commit()

            checked_report = assemble_comparison_report(
                analysis=analysis,
                result=processing_result,
            )

            job.result = checked_report.model_dump(mode="json")
            job.status = "succeeded"
            job.stage = "finished"
            job.error_message = None
            job.finished_at = func.now()

            db.commit()

        except Exception as error:
            db.rollback()

            failed_job = get_report_job(db, job_id)

            if failed_job is not None:
                failed_job.status = "failed"
                failed_job.stage = "failed"
                failed_job.error_message = str(error)[:2000]
                failed_job.finished_at = func.now()

                db.commit()

            raise
