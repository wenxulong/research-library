from io import BytesIO
from threading import Lock

from matplotlib import rc_context
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from schemas import ExperimentStatisticsResponse


_plot_lock = Lock()

def build_experiment_chart(
    statistics: ExperimentStatisticsResponse,
    dataset: str,
    scenario: str,
) -> bytes:
    """绘制指定数据集和场景下的方法对比图。"""

    dataset = dataset.strip()
    scenario = scenario.strip()

    selected = [
        group
        for group in statistics.groups
        if group.dataset == dataset
        and group.scenario == scenario
    ]

    if not selected:
        raise ValueError("没有找到指定数据集和场景的统计结果")

    groups = [
        group
        for group in selected
        if group.mean is not None
    ]

    if not groups:
        raise ValueError("该数据集和场景没有可绘制的有效指标")

    omitted_methods = [
        group.method
        for group in selected
        if group.mean is None
    ]

    # 优先使用系统已有的中文字体；设置只在本次绘图中生效。
    with _plot_lock, rc_context({
        "font.family": "sans-serif",
        "font.sans-serif": [
            "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC",
            "SimHei", "Arial Unicode MS", "DejaVu Sans",
        ],
        "axes.unicode_minus": False,
    }):
        figure = Figure(
            figsize=(8, 5),
            dpi=150,
        )

        FigureCanvasAgg(figure)

        ax = figure.subplots()

        positions = list(range(len(groups)))

        labels = [
            f"{group.method}\n(n={group.valid_count})"
            for group in groups
        ]

        means = [
            group.mean
            for group in groups
        ]

        ax.bar(
            positions,
            means,
            width=0.6,
            color="#4C78A8",
        )

        for position, group in zip(positions, groups):
            if group.stddev is not None:
                ax.errorbar(
                    position,
                    group.mean,
                    yerr=group.stddev,
                    fmt="none",
                    ecolor="#222222",
                    capsize=5,
                )

        ax.set_xticks(positions, labels)
        ax.set_xlabel("Method")
        ax.set_ylabel(statistics.metric)

        ax.set_title(
            f"Dataset: {dataset} | Scenario: {scenario}"
        )

        ax.grid(axis="y", alpha=0.25)
        ax.set_axisbelow(True)

        note = (
            "Bars: mean; whiskers: sample SD; n: valid rows.\n"
            "No whisker when fewer than 2 valid rows."
        )

        if omitted_methods:
            note += (
                "\nNo valid data: "
                + ", ".join(omitted_methods)
            )

        figure.text(
            0.02,
            0.02,
            note,
            fontsize=8,
        )

        figure.tight_layout(
            rect=(0, 0.14, 1, 1),
        )

        with BytesIO() as buffer:
            figure.savefig(
                buffer,
                format="png",
            )

            return buffer.getvalue()
