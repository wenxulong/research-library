"""停止API和本项目全部Celery Worker后，手动恢复论文准备作业。"""

from research_library.db.database import SessionLocal
from research_library.repositories.paper_preparation_job_crud import get_preparation_job, recover_interrupted_preparation_job


def main() -> None:
    """按指定编号恢复；确认输入只表示人工核实，不自动检测进程。"""

    raw_job_id = input("请输入要恢复的论文准备作业编号：").strip()
    try:
        job_id = int(raw_job_id)
    except ValueError:
        print("作业编号必须是整数。")
        return

    if job_id <= 0:
        print("作业编号必须大于0。")
        return

    with SessionLocal() as db:
        job = get_preparation_job(db=db, job_id=job_id)
        if job is None:
            print("没有找到这个论文准备作业。")
            return

        print(f"作业编号：{job.id}")
        print(f"论文编号：{job.paper_id}")
        print(f"PDF文档编号：{job.document_id}")
        print(f"当前状态：{job.status}")
        print(f"当前阶段：{job.stage}")

        if job.status not in ("queued", "running"):
            print("这个作业已经结束，不需要恢复。")
            return

        confirmation = input(
            "确认API和本项目所有Celery Worker已停止，"
            "输入“确认恢复”继续："
        ).strip()
        if confirmation != "确认恢复":
            print("已取消恢复。")
            return

        recovered = recover_interrupted_preparation_job(db=db, job_id=job_id)
        if recovered:
            print("旧作业已标记为失败。")
            print("重启服务后，可以重新提交这篇论文的准备作业。")
        else:
            print("作业状态已经变化，本次没有修改记录。")


if __name__ == "__main__":
    main()
