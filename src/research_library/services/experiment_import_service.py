import csv
import io
from pathlib import Path

from sqlalchemy.orm import Session

from research_library.db.model import ExperimentImport, ResearchQuestionSet
from research_library.schemas import ResearchPlanResponse

def parse_experiment_csv(
    text: str,
    required_columns: list[str],
) -> list[dict[str, str]]:
    """检查CSV结构，按列名解析每条记录。"""

    reader = csv.reader(
        io.StringIO(text, newline=""),
        strict=True,
    )

    try:
        headers = next(reader)
    except StopIteration:
        raise ValueError("CSV文件为空")
    except csv.Error as error:
        raise ValueError("CSV表头格式不正确") from error

    headers = [name.strip() for name in headers]

    if not headers or any(not name for name in headers):
        raise ValueError("CSV列名不能为空")

    if len(headers) != len(set(headers)):
        raise ValueError("CSV存在重复列名")

    missing = set(required_columns) - set(headers)

    if missing:
        raise ValueError(
            "CSV缺少研究计划要求的列："
            + "、".join(sorted(missing))
        )

    rows = []

    try:
        for values in reader:
            if not values:
                continue

            if len(values) != len(headers):
                raise ValueError(
                    f"CSV第{reader.line_num}行对应记录的列数与表头不一致"
                )

            row = dict(
                zip(
                    headers,
                    [value.strip() for value in values],
                )
            )

            rows.append(row)

    except csv.Error as error:
        raise ValueError("CSV内容格式不正确") from error

    if not rows:
        raise ValueError("CSV只有表头，没有实验结果")

    return rows

def import_experiment_results(
    db: Session,
    record: ResearchQuestionSet,
    filename: str,
    raw_csv: str
) -> ExperimentImport:
    """根据当前计划检查CSV，并保存一次独立导入。"""

    if record.plan is None:
        raise ValueError("请先生成研究计划")

    plan = ResearchPlanResponse.model_validate(
        record.plan
    )

    rows = parse_experiment_csv(
        raw_csv,
        plan.content.result_columns,
    )

    imported = ExperimentImport(
        question_set_id=record.id,
        filename=Path(filename).name,
        raw_csv=raw_csv,
        plan_snapshot=plan.model_dump(mode="json"),
        rows=rows,
    )

    try:
        db.add(imported)
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(imported)

    return imported
