from database import SessionLocal
from report_job_crud import (
    get_report_job,
    recover_interrupted_job,
)

def main() -> None:
    """查询指定作业，在确认Worker停止后手动恢复。"""

    raw_job_id = input("请输入要恢复的作业编号：").strip()

    try:
        job_id = int(raw_job_id)
    except ValueError:
        print("作业编号必须是整数。")
        return

    with SessionLocal() as db:
        job = get_report_job(db, job_id)

        if job is None:
            print("没有找到这个作业。")
            return

        print(f"作业编号：{job.id}")
        print(f"研究任务编号：{job.task_id}")
        print(f"当前状态：{job.status}")
        print(f"当前阶段：{job.stage}")

        if job.status not in ("queued", "running"):
            print("这个作业已经结束，不需要恢复。")
            return

        confirmation = input(
            "确认API和所有报告Worker已停止，"
            "输入“确认恢复”继续："
        ).strip()

        if confirmation != "确认恢复":
            print("已取消恢复。")
            return

        recovered = recover_interrupted_job(db, job_id)

        if recovered:
            print("旧作业已标记为失败，可以重新提交研究任务。")
        else:
            print("作业状态已经变化，本次没有修改记录。")


if __name__ == "__main__":
    main()
