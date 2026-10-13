from sqlalchemy import func

from celery_app import celery_app
from database import SessionLocal
from embedding_service import EMBEDDING_MODEL
from model import PaperDocument
from schemas import PaperPrepareResponse

import paper_pipeline
import paper_preparation_job_crud

@celery_app.task(name="papers.run_preparation_job")
def run_preparation_job(job_id: int) -> None:
    """执行论文准备作业，把阶段、结果和错误保存到MySQL。"""

    with SessionLocal() as db:
        job = paper_preparation_job_crud.claim_preparation_job(
            db=db,
            job_id=job_id,
        )

        if job is None:
            return

        try:
            document = db.get(
                PaperDocument,
                job.document_id,
            )

            if document is None:
                raise ValueError("提交作业时的PDF文档已经不存在")

            if document.paper_id != job.paper_id:
                raise ValueError("PDF文档与作业所属论文不一致")

            def update_stage(stage: str) -> None:
                job.stage = stage
                db.commit()

            result = paper_pipeline.prepare_paper_document(
                db=db,
                document=document,
                on_stage=update_stage,
            )

            response = PaperPrepareResponse(
                paper_id=job.paper_id,
                document_id=job.document_id,
                model_name=EMBEDDING_MODEL,
                page_count=result.page_count,
                chunk_count=result.chunk_count,
                indexed_count=result.indexed_count,
            )

            job.result = response.model_dump(mode="json")
            job.status = "succeeded"
            job.stage = "finished"
            job.error_message = None
            job.finished_at = func.now()

            db.commit()

        except Exception as error:
            db.rollback()

            failed_job = paper_preparation_job_crud.get_preparation_job(
                db=db,
                job_id=job_id,
            )

            if failed_job is not None:
                failed_job.status = "failed"
                failed_job.stage = "failed"
                failed_job.error_message = str(error)[:2000]
                failed_job.finished_at = func.now()

                db.commit()

            raise