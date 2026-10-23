from math import isfinite
from statistics import mean, stdev

from research_library.db.model import ExperimentImport
from research_library.schemas import ExperimentGroupStatistics, ExperimentStatisticsResponse

def summarize_experiment_results(
    imported: ExperimentImport,
    metric: str,
) -> ExperimentStatisticsResponse:
    """按方法、数据集和场景统计一个数值指标。"""

    metric = metric.strip()

    if not metric:
        raise ValueError("指标名称不能为空")

    if not imported.rows:
        raise ValueError("这次导入没有实验数据")

    required_columns = {"method", "dataset", "scenario", metric}
    available_columns = set(imported.rows[0])

    missing_columns = required_columns - available_columns

    if missing_columns:
        raise ValueError(
            "实验数据缺少统计所需的列："
            + "、".join(sorted(missing_columns))
        )

    status_available = "status" in available_columns

    grouped_rows = {}

    for row in imported.rows:
        key = (
            row["method"],
            row["dataset"],
            row["scenario"],
        )

        if any(not part.strip() for part in key):
            raise ValueError("method、dataset、scenario不能为空")

        grouped_rows.setdefault(key, []).append(row)

    groups = []

    for key, rows in grouped_rows.items():
        values = []

        failed_count = 0
        missing_count = 0
        invalid_count = 0
        unknown_status_count = 0

        for row in rows:
            if status_available:
                status = row["status"].strip().casefold()

                if status == "failed":
                    failed_count += 1
                    continue

                if status != "success":
                    unknown_status_count += 1
                    continue

            raw_value = row[metric].strip()

            if not raw_value:
                missing_count += 1
                continue

            try:
                value = float(raw_value)
            except ValueError:
                invalid_count += 1
                continue

            if not isfinite(value):
                invalid_count += 1
                continue

            values.append(value)

        groups.append(
            ExperimentGroupStatistics(
                method=key[0],
                dataset=key[1],
                scenario=key[2],
                total_count=len(rows),
                valid_count=len(values),
                failed_count=failed_count,
                missing_count=missing_count,
                invalid_count=invalid_count,
                unknown_status_count=unknown_status_count,
                mean=mean(values) if values else None,
                stddev=stdev(values) if len(values) >= 2 else None,
            )
        )

    return ExperimentStatisticsResponse(
        import_id=imported.id,
        metric=metric,
        status_available=status_available,
        groups=groups,
    )
