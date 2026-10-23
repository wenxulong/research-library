# 目录与调用关系

后端使用 src 布局，包名为 `research_library`。在项目根目录执行 `uv sync --locked` 安装，之后代码通过完整包路径导入，例如 `from research_library.db.database import get_db`。PyCharm选择项目的 `.venv` 解释器；如果编辑器不能识别导入，将 `src` 标记为 Sources Root。

## 一条请求的路径

浏览器 → `main.py` 创建的 FastAPI 应用 → `api/` 中的路由 → `services/` 业务流程 → `repositories/` 数据读写 → MySQL。

- `schemas/` 定义输入输出，`db/model.py` 定义数据库表对应的对象。
- `agents/` 负责模型参与的分析、审校、研究计划、写作和证据调查。
- `tools/` 提供Agent可执行的工具，`retrieval/` 提供检索和RAG能力。
- 部分简单路由直接调用数据库读写函数，不为目录层级额外增加转发函数。

## 后台任务

API保存作业 → 投递Celery → `workers/` 执行现有业务流程 → MySQL保存状态和结果 → 浏览器查询。

启动：`python manage.py api`；另一个终端运行 `python manage.py worker`。这两个命令均在项目根目录、虚拟环境中执行。Celery注册名仍为 `reports.run_report_job` 和 `papers.run_preparation_job`，目录迁移没有改变队列和数据库作业编号。

## 维护与验证

- 初始化/升级数据库：`python manage.py init`。
- 检查环境：`python manage.py check`。
- 后端测试：`python -B -m unittest discover -s tests -t . -q`。
- 人工恢复脚本：`python -B -m scripts.recover_report_job` 或 `python -B -m scripts.recover_paper_preparation_job`；先按部署文档停止相关Worker再核对恢复。
- 前端仍在 `frontend/` 中执行 `npm test`、`npm run build`。

`migrations/`、`skills/`、前端构建文件以及本地配置和上传文件均保留在根目录。当前包按源码检出方式运行，部署时需要完整项目目录；暂未支持单独分发一个wheel后运行。
