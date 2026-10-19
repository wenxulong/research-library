# 科研资料库 · Research Library

一个基于 **FastAPI、Vue 3 和大模型**的个人科研工作台，将论文 PDF 转换成可检索的研究资料，围绕研究问题完成问答、结构化分析、原文审校和多论文对比，再衔接候选问题、研究方案、实验结果分析与带引用的论文初稿写作。

项目重点是让研究结论能够追溯到论文原文：回答和报告携带来源，审校检查陈述是否得到证据支持，证据不足时尝试补充资料并复审。模型输出仍需要研究者核对，不能替代实际阅读与实验验证。

当前版本主要面向 Mac 本机个人使用；Linux 提供部署模板，尚未完成实机验收。

## 功能

| 模块 | 已实现能力 |
| --- | --- |
| 论文资料库 | 添加、编辑、搜索与删除论文信息，上传和删除 PDF |
| 论文准备 | 后台提取逐页正文、文本分块、生成并保存向量；复用已有准备数据 |
| 检索与问答 | 在研究任务关联的论文中进行语义检索、关键词辅助排序和 RAG 问答 |
| 结构化分析 | 整理概述、方法、实验、结论、局限与证据备注，保存原文来源 |
| 原文审校 | 逐条判断证据支持、部分支持、证据不足或与原文矛盾 |
| 修订与版本 | 生成修订建议及复审结果；将合格陈述移动到证据备注，记录版本并支持恢复 |
| 多论文对比 | 对比方法、实验与结论，按需补充证据、修正并复审 |
| 作业与报告 | 查询后台阶段、结果和错误，展示对比报告并导出 Markdown |
| 研究问题与方案 | 根据对比报告提出候选研究问题，用户选择后生成方法、基线、数据、指标及实验计划 |
| 实验结果 | 导入实际运行得到的CSV，按方法、数据集和场景统计多个指标，生成图表与分析 |
| 研究报告 | 将方案、统计、图表与分析打包为带证据的研究报告ZIP |
| 论文写作 | 生成并保存中文Markdown初稿，读取、章节扩写、确认保存及ZIP下载 |
| 问答缓存 | 在资料范围内尝试完全匹配和带实体、意图等条件校验的语义复用 |
| 原文追溯 | 根据资料引用查看对应文本块和所在页正文 |
| 运行维护 | 统一配置、Alembic 数据库迁移、健康检查、日志与人工中断恢复 |

修订建议不会自动全部覆盖分析；当前采纳接口支持符合条件的陈述移动到证据备注。

## 工作流程

```mermaid
flowchart TD
    A[添加论文并上传 PDF] --> B[后台准备：正文提取、分块、向量化]
    B --> C[创建研究任务并关联论文]
    C --> D[围绕研究问题检索原文]
    D --> E[RAG 问答]
    D --> F[生成并保存结构化分析]
    F --> G[分析审校、修订建议与人工采纳]
    F --> H[提交后台对比报告作业]
    H --> I[生成对比、审校、按需补证与修正复审]
    I --> J[保存报告、核对原文来源、导出 Markdown]
    J --> K[提出候选研究问题，用户选择]
    K --> L[生成研究方案和实验计划]
    L --> M[用户实际运行实验，导入CSV]
    M --> N[统计、图表与实验分析]
    N --> O[导出带引用研究报告ZIP]
    N --> P[生成并保存论文初稿]
    P --> Q[章节扩写建议，人工确认后保存和下载]
```

第六阶段报告导出与第七阶段写作复用实验材料；写作不要求先点击导出研究报告。详细步骤见[七阶段使用指南](USER_GUIDE.md)。

问答是一条独立使用分支，不必先问答才能生成分析。后台对比报告会执行自己的审校流程，不要求先手动将分析审校到全部通过。

## 技术栈与架构

| 层级 | 技术 | 作用 |
| --- | --- | --- |
| 前端 | Vue 3、Vite、JavaScript | 论文管理、研究任务、作业、研究方案、实验结果、论文写作与服务状态页面 |
| API | Python、FastAPI、Pydantic、Uvicorn | 接收请求、校验数据、协调业务模块 |
| 数据存储 | MySQL、SQLAlchemy、PyMySQL | 保存论文、正文、文本块、向量、分析、版本、作业、研究方案、实验导入及初稿快照 |
| 数据库迁移 | Alembic | 初始化、接管已有结构并升级数据库版本 |
| 后台执行 | Celery、Redis | Redis传递作业消息并存放问答缓存，Worker执行论文准备和报告流程 |
| PDF 解析 | PyMuPDF | 按页提取 PDF 中已有的文字 |
| 向量模型 | Ollama、`qwen3-embedding:0.6b` | 在本机生成文本与问题向量 |
| 实验统计与绘图 | Python statistics、Matplotlib | 分组均值、样本标准差和PNG图表 |
| 生成模型 | DeepSeek、OpenAI Python SDK | 生成回答、分析、对比、审校、研究方案、实验解读和论文正文 |

PDF 文件存放在磁盘，文件元数据和提取后的资料存入 MySQL。向量目前也保存在 MySQL 中，检索时由 Python 计算相似度并排序；项目未使用独立向量数据库。Redis同时用于队列和问答缓存；正式报告与初稿保存在MySQL，不以Redis作为持久化结果库。语义缓存使用Redis的`VADD`/`VSIM`命令，所连接服务须支持这些命令；不支持时跳过语义缓存，继续原有RAG流程。

分析、对比、审校和修订 Agent 由 Python 函数按预定流程协调执行。目前没有实现自由规划的通用 Agent，也没有接入 Skill 自动选择系统。

## 环境要求

- **Python 3.14 或以上**：以 `pyproject.toml` 为准。
- **uv**：安装 Python 依赖并使用 `uv.lock` 锁定版本。
- **Node.js 22.12 或以上及 npm**：前端也接受 Node.js 20.19 系列，准确范围见 `frontend/package.json`。
- 可连接的 **MySQL** 与 **Redis** 服务。
- 已安装并运行的 **Ollama**，以及配置中的向量模型。
- **DeepSeek API 密钥**：模型生成类功能需要；论文管理、准备和本地检索不需要该密钥。

安装工具参考：[Python](https://www.python.org/downloads/)、[uv](https://docs.astral.sh/uv/getting-started/installation/)、[Node.js](https://nodejs.org/)、[MySQL](https://dev.mysql.com/downloads/)、[Redis](https://redis.io/docs/latest/operate/oss_and_stack/install/install-redis/)、[Ollama](https://ollama.com/)。下面的命令以 macOS/Linux 终端为例，Windows 启动流程尚未验收。

## 快速开始

### 1. 获取代码与安装依赖

克隆项目并安装锁定的依赖：

```bash
git clone https://github.com/wenxulong/research-library.git research-library
cd research-library
uv sync --locked
```

也可以下载仓库 ZIP，解压后进入项目根目录，再执行 `uv sync --locked`。项目目录中应能看到 `manage.py`、`pyproject.toml` 和 `frontend/`。

### 2. 准备外部服务

启动 MySQL、Redis 和 Ollama。下载默认向量模型：

```bash
ollama pull qwen3-embedding:0.6b
```

该模型需要从网络下载；大小和耗时取决于模型版本及网络。后续向量计算通过本机 Ollama 服务执行。

MySQL 数据库需要预先存在，应用账号需要在该数据库内创建表、执行迁移并读写数据的权限。**下面的 SQL 仅用于全新安装**；已有数据库和账号可直接使用，无需重新创建。

用管理员账号进入 MySQL：

```bash
mysql -u root -p
```

在 MySQL 提示符中执行，将 `YOUR_PASSWORD` 替换成自己的应用数据库密码：

```sql
CREATE DATABASE IF NOT EXISTS research_library CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'research_app'@'127.0.0.1' IDENTIFIED BY 'YOUR_PASSWORD';
GRANT ALL PRIVILEGES ON research_library.* TO 'research_app'@'127.0.0.1';
EXIT;
```

`CREATE USER IF NOT EXISTS` 不会修改已有账号的密码；配置时必须使用该账号实际密码。权限仅授予项目数据库，不要将管理员账号作为应用账号。

### 3. 配置后端

在项目根目录复制配置模板。`-n` 避免覆盖已经存在的 `.env`：

```bash
cp -n .env.example .env
```

编辑 `.env`，至少核对以下配置：

```dotenv
DATABASE_URL=mysql+pymysql://research_app:YOUR_PASSWORD@127.0.0.1:3306/research_library?charset=utf8mb4
REDIS_URL=redis://127.0.0.1:6379/0
OLLAMA_BASE_URL=http://127.0.0.1:11434
EMBEDDING_MODEL=qwen3-embedding:0.6b
DEEPSEEK_API_KEY=YOUR_DEEPSEEK_API_KEY
```

使用自己的数据库密码和模型密钥；仅使用本地功能时可以将 `DEEPSEEK_API_KEY` 留空。密码包含 `@`、`#` 等 URL 特殊字符时，需要在数据库连接 URL 中进行编码。

其余配置以 [.env.example](.env.example) 为准。`DEEPSEEK_BASE_URL` 和 `DEEPSEEK_MODEL` 必须对应实际可用的服务与模型。环境变量优先于项目根目录 `.env`，相对文件路径始终相对项目根目录。

数据库密码和模型密钥仅保存在后端配置中，不要放进前端 `VITE_` 环境变量。

### 4. 初始化数据库

在项目根目录执行：

```bash
.venv/bin/python -B manage.py init
```

程序通过 Alembic 管理表结构：新数据库执行迁移；已有数据库先核对结构，再登记或升级版本。无法识别的结构差异会使初始化停止，不会自动删除表。

当前迁移版本为 `0006_paper_drafts`，已包含研究问题集、研究计划、实验导入和论文初稿存储。已有数据的环境升级前，应备份数据库与上传文件、停止 API 和所有 Worker；详细说明见 [DEPLOYMENT.md](DEPLOYMENT.md)。

### 5. 启动 API、Worker 和前端

开发模式需要三个终端，MySQL、Redis 和 Ollama 应已启动。

**终端一：项目根目录，启动 API。**

```bash
.venv/bin/python -B manage.py api
```

**终端二：项目根目录，启动 Worker。**

```bash
.venv/bin/python -B manage.py worker
```

Worker 应注册 `papers.run_preparation_job` 和 `reports.run_report_job`，并消费配置中的队列，默认是 `report_jobs`。当前启动命令使用单进程 `solo` 模式。

**终端三：从项目根目录进入前端目录，安装依赖并启动。**

```bash
cd frontend
npm ci
npm run dev
```

默认不需要复制前端环境模板；Vite 会代理到 `http://127.0.0.1:8000`。后端端口不同才需要参考 [frontend/.env.example](frontend/.env.example) 配置 `VITE_API_TARGET`。

打开：

- 开发页面：[http://127.0.0.1:5173/app/](http://127.0.0.1:5173/app/)。
- 接口文档：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)。

可以在另一个终端中进入项目根目录检查依赖：

```bash
.venv/bin/python -B manage.py check
```

该命令检查数据库及迁移版本、Redis、Ollama模型和Worker。退出码0表示全部确认可用；异常或无法确认时退出码为1。DeepSeek检查仅确认密钥已配置，不验证远程服务、余额或密钥有效性。

### 6. 使用构建后的前端

在 `frontend/` 目录执行：

```bash
npm run build
```

保持 API 与 Worker 运行，打开 [http://127.0.0.1:8000/app](http://127.0.0.1:8000/app)。FastAPI 从 `frontend/dist` 提供页面和静态资源，这种模式不需要运行 Vite 开发服务器。

修改后端代码或配置后需要重启对应服务；仅替换构建资源不要求修改后端代码。若浏览器仍显示旧入口，刷新页面。没有构建产物时，`/app` 返回503并提示构建命令。

## 第一次使用

1. 在“论文资料库”添加论文信息并上传 PDF。默认单文件上限20MB，可通过 `MAX_UPLOAD_MB` 调整。
2. 点击“后台准备”，等待完成。系统提取正文、分块并索引向量；上传成功不等于准备完成。
3. 创建研究任务，填写研究问题，关联已准备的论文。
4. 在“问答与检索”中试用本地检索，核对召回原文；RAG问答会调用DeepSeek。
5. 在“分析与审校”生成分析，按需审校、查看修订建议并采纳合格移动建议。
6. 需要多论文报告时，关联至少两篇论文，并确保已保存分析实际引用的证据覆盖至少两篇论文，再提交后台报告作业。
7. 在“作业与报告”查看阶段及结果，核对报告的审校状态，点击资料来源检查原文，再导出Markdown。

报告完成有两个不同含义：作业 `status="succeeded"` 表示流程执行并保存结果；报告 `passed=true` 才表示本轮审校通过。`passed=false` 的报告会显示为待核验，不能因为作业成功就将其当作已验证结论。

资料编号 `[资料N]` 是当前分析或报告中的引用编号，与数据库 `paper_id`（论文编号）、`chunk_id`（文本块编号）不同。对比报告保留引用编号，页面据此定位真实来源。

未命中缓存的问答，以及分析、审校、修订、采纳核验、报告、候选问题、研究计划、实验解读、初稿生成和章节扩写，会将相应研究材料发送到配置中的DeepSeek服务，可能产生API费用。读取和下载已保存初稿、确认保存正文不会调用模型。请使用有权处理的资料，并确认这些内容适合发送到该服务。

## 数据一致性与作业恢复

- 准备作业未结束时，不能删除或重建该论文的准备数据。
- 报告排队或执行期间，所有关联论文都受保护，包括可能用于补证的论文；不能删除、重建资料或增减关联。
- 增减关联后，已有分析标记为 `stale`（过期），需要重新分析后才能继续审校、恢复或提交新报告。
- 审校和重新分析保存前比较输入状态，输入已变化时返回409，避免覆盖较新的结果。
- 有未结束报告作业的研究任务不能删除；删除已结束任务会清理该任务的分析、版本与报告作业历史，论文及PDF保留。
- Worker中断后，需要人工确认并恢复作业，重新启动进程不会自动清理旧的 `running` 状态。

确认服务中断后，先停止API及本项目**所有Worker**，再在项目根目录运行对应工具：

```bash
.venv/bin/python -B recover_paper_preparation_job.py
```

```bash
.venv/bin/python -B recover_report_job.py
```

输入的是相应类型的**作业编号**，不是论文或研究任务编号。脚本要求人工核对确认，不自动证明Worker已停止；只结束指定未完成记录，不删除页面、块和向量。随后启动服务并提交新作业。详细步骤见 [DEPLOYMENT.md](DEPLOYMENT.md)。

## 自动化测试

后端：在项目根目录执行，覆盖全部 `test_*.py` 文件。

```bash
.venv/bin/python -B -m unittest discover -q
```

前端：在 `frontend/` 目录执行。

```bash
npm test
npm run build
```

最近一次本机验证记录（2026-10-19）：后端578项、前端94项测试通过，前端构建成功。已用独立演示记录完成章节扩写、编辑建议、确认保存、刷新恢复及ZIP一致性的真实模型联调；演示统计为合成数据，不作为科研结果。测试数量会随代码更新变化。

后端测试使用独立SQLite及服务模拟，不要将测试建表/删表逻辑改为连接正式数据库。测试通过不能替代MySQL并发验收、远程模型联调或报告质量评估。

## 项目结构

```text
research-library/
├── api.py                         # FastAPI接口与业务协调
├── config.py                      # 统一配置
├── database.py / model.py          # 数据库连接和ORM模型
├── schemas.py                     # 请求、响应和模型输出结构
├── *_crud.py                      # 数据库操作
├── paper_pipeline.py              # PDF准备流程
├── paper_preparation_worker.py     # 论文准备后台任务
├── semantic_search.py             # 语义检索与排序
├── rag_service.py                 # 论文问答
├── paper_analysis_agent.py        # 结构化分析
├── review_* / revision_agent.py   # 原文审校与修订
├── comparison_*.py                # 多论文对比与报告流程
├── evidence_*.py                  # 证据补充与审校
├── report_worker.py               # 后台报告任务
├── report_export.py               # Markdown导出
├── research_question_* / research_plan_service.py # 候选问题、选择与方案
├── experiment_*                   # CSV导入、统计、图表和实验分析
├── research_report_export.py      # 研究报告ZIP
├── paper_draft_*                  # 写作材料、初稿、扩写、保存与导出
├── cache_context.py / semantic_cache.py # 问答缓存条件和Redis读写
├── migrations/                    # Alembic版本
├── frontend/                      # Vue页面、组件、API封装和测试
├── deploy/systemd/                # Linux服务模板
├── test_*.py                      # 后端自动化测试
├── .env.example                   # 后端配置模板
└── LICENSE                        # AGPL-3.0标准文本
```

## 常见问题

**5173 与8000的页面有什么区别？**

5173是Vite开发页面，可即时反映源码修改；8000是API提供的构建页面，需要先执行 `npm run build`。它们访问同一个后端，但浏览器作业跟踪记录按站点分别存储，换入口后可能需要按作业编号重新载入。

**为什么健康检查显示Worker“未确认”？**

单进程solo Worker执行长任务时可能无法及时回复控制命令。检查超时不代表Worker已死，请结合阶段、作业结果和 `logs/worker.log` 判断，不要据此强行结束进程。

**为什么页面停止等待后，作业仍在运行？**

停止等待或请求超时只结束浏览器等待，不取消服务器执行。结果未确认时先刷新或查询状态，避免重复提交。

**扫描版PDF为什么没有正文？**

当前只提取PDF已有文字，没有OCR流程。纯图片、加密或损坏的PDF可能无法完成准备；图表、公式和复杂排版也可能提取不完整。

## 当前限制

- 没有多用户登录、权限控制和数据隔离，当前默认仅绑定本机地址。
- 作业页面支持按论文或研究任务查询服务端历史并翻页；问题集、实验导入和初稿目前通过编号或浏览器记忆恢复，尚无完整的历史管理页面。
- 分析、研究方案、实验解读、初稿生成和章节扩写等模型操作仍为同步请求；论文准备和对比报告已经后台化。
- 当前检索在Python中计算已有向量的相似度，尚未针对大规模论文库优化。
- 没有联网论文搜索、OCR、自动实验执行或通用自主规划功能。
- 原文审校与修订仍依赖模型判断；初稿和扩写检查JSON结构、字段及资料编号，不自动证明每句话均获原文支持。
- 当前写作导出Markdown和JSON及图表，不提供Word、LaTeX投稿排版或自动投稿。扩写字数为近似引导；人工确认后原地保存，不建立初稿修订历史。
- Linux systemd配置是模板，尚未在Linux完成部署验收；也未提供一键Docker部署。

## 进一步阅读

- [七阶段使用指南、实验CSV与章节扩写](USER_GUIDE.md)
- [部署、迁移、日志与故障恢复](DEPLOYMENT.md)
- [前端开发、构建和页面说明](frontend/README.md)
- [配置模板](.env.example)

## 开源许可证

本项目原创代码采用 **GNU Affero General Public License v3.0（AGPL-3.0-only）**，完整条款见 [LICENSE](LICENSE)。允许按许可证条款学习、使用、修改和商业使用；分发受覆盖的版本时须遵守对应源码及声明等要求。修改后通过网络向用户提供服务时，须按第13条向交互用户提供获取对应源码的途径。

第三方依赖保留各自的许可证。PDF解析使用的 [PyMuPDF](https://github.com/pymupdf/PyMuPDF) 提供AGPL-3.0或商业许可，本项目使用其开源版本。本项目许可证不改变第三方依赖、模型、上传论文及其他外部资料各自的许可与权利。

真实API密钥、数据库密码、上传论文、运行数据和日志不作为项目源码发布，相关文件通过 `.gitignore` 排除。发布前仍需核对其他依赖的许可与声明要求。
