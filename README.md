# Research Library · 科研多 Agent 工作台

从论文 PDF 出发，完成文献检索、证据审校、多论文对比、研究规划、实验结果分析与带引用的论文初稿写作。

项目采用 **Python 原生编排的多角色协作流程 + 局部自主 ReAct Agent**：研究主线由明确的业务流程组织，用户可针对报告中的一条陈述启动自主查证，让模型选择检索、阅读上下文和记忆工具，再由独立审校环节检查证据。前端使用 Vue 3，后端使用 FastAPI。

> 当前面向个人科研使用。系统辅助研究者整理和核对材料；研究问题的价值、实际实验与最终论文内容仍由研究者确认。

## 核心能力

| 能力 | 实现内容 |
| --- | --- |
| 论文知识库 | PDF 上传、逐页 Markdown 提取、版面与表格解析、按需本地 OCR、正文及表格分块、向量索引 |
| 混合检索与 RAG | 在当前研究任务关联论文内独立执行向量与 BM25 召回，通过 RRF 融合、参考文献降权和页内多样性选择结果；回答保留来源 |
| 多角色协作 | 论文分析、对比、审校、修订、研究问题、研究方案、实验分析与写作等模型角色，通过结构化结果衔接 |
| 自主证据调查 | 用户提出查证要求，ReAct Agent 多轮选择工具、观察结果、调整行动；保存独立候选，用户决定是否采用 |
| 共享记忆 | 类实现的存储、检索、上下文管理与经验提取；查证、研究规划、实验分析、初稿和扩写按需复用 |
| 上下文工程 | 区分规则、当前任务、论文原文与历史记忆；原文去重、证据预算、记忆预算和引用编号映射 |
| 研究与实验 | 从通过审校的对比报告提出候选问题，用户选择后生成研究计划；导入实际实验 CSV，计算统计、绘图和分析差异 |
| 写作与 Skill | 组装研究材料，生成中文 Markdown 初稿；加载本地 Skill 识别章节扩写要求，生成建议，确认后保存与导出 |
| 作业与缓存 | Celery 后台执行论文准备及对比报告；MySQL 保存输入、阶段和结果；Redis 承载消息与问答缓存 |
| 工程支持 | Alembic 迁移、输入快照、分析版本、操作冲突控制、健康检查、用量日志、自动化测试及维护脚本 |

没有依赖 LangChain / LangGraph / RAGFlow 来编排 Agent。各角色并非全部拥有自主工具循环；目前明确采用自主工具调度的是证据查证环节。

## 研究工作流

```mermaid
flowchart TD
    A[上传论文 PDF] --> B[后台准备：解析、分块、向量化]
    B --> C[创建研究任务并关联论文]
    C --> D[RAG 问答与原文检索]
    C --> E[结构化论文分析]
    E --> F[分析审校、修订与版本记录]
    E --> G[后台多论文对比报告]
    G --> H[逐条审校、必要补证、修正与复审]
    H --> I[用户核对报告]
    I --> J[可选：ReAct 自主查证单条陈述]
    J --> K[候选调查与复审结果]
    K --> L[用户确认采用]
    L --> I
    I --> M[通过审校的报告生成候选研究问题]
    M --> N[用户选择问题，形成研究计划]
    N --> O[用户实际运行实验并导入 CSV]
    O --> P[统计、图表与实验分析]
    P --> Q[导出研究报告 ZIP]
    P --> R[生成带引用论文初稿]
    R --> S[编辑或扩写建议，确认保存与下载]
```

- 问答是独立分支，不是生成分析的前置操作。
- 对比报告会执行自己的审校流程，不要求用户先手动完成所有分析审校。
- 作业 `succeeded` 表示执行完成；报告 `passed` 表示本轮审校通过，两者含义不同。
- 研究计划描述“准备怎么做”，实际实现说明、实验条件和结果需要用户提供。
- 写作直接复用实验材料，不要求先导出研究报告。

### 自主查证如何工作

```text
当前陈述 + 已有证据 + 审校意见 + 用户要求 + 辅助记忆
                         ↓
                    模型选择行动
                         ↓
      检索论文 / 读取相邻块 / 查询记忆 / 保存经验
                         ↓
                 工具结果回传模型
                         ↓
              继续查证或汇总证据缺口
                         ↓
            原文复审 → 保存候选 → 用户采用
```

模型通过原生 Tool Calling 返回工具名和参数，Python 执行实际操作。循环受轮次及证据预算约束，工具只能访问当前任务允许的资料。反对证据与适用条件独立展示，不能把矛盾来源当成普通支持引用。

### 共享记忆如何工作

`MemoryRepository` 负责存取，`MemoryRetriever` 负责相关性检索，`MemoryManager` 负责选择、精确查重和上下文预算，`MemoryExtractor` 从已保存的调查中提炼事件或经验。

- 查证可初始召回，也可通过工具主动查询、保存记忆。
- 规划复用约束与经验；实验分析读取相关经验；写作和扩写读取偏好与相关经验。
- 记忆按归属和研究任务范围过滤；当前应用仍以 `local-user` 作为本地归属，并未实现多用户登录。
- 记忆是辅助材料，不能替代论文原文、真实统计或本次用户要求；记忆编号不是论文引用编号。
- 这属于持久化信息复用，不是训练或更新模型参数。

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 前端 | Vue 3、Vite、JavaScript、Fetch API |
| 后端 | Python 3.14+、FastAPI、Pydantic、Uvicorn |
| 持久化 | MySQL、SQLAlchemy、PyMySQL、Alembic |
| 后台任务与缓存 | Celery、Redis |
| PDF 与 OCR | PyMuPDF、PyMuPDF4LLM、RapidOCR |
| 检索 | Ollama Embedding、余弦相似度、BM25、RRF |
| 模型调用 | DeepSeek、OpenAI Python SDK 兼容接口、Tool Calling、结构化输出校验 |
| 实验分析 | Python statistics、Matplotlib |
| 测试 | unittest、独立 SQLite、接口与外部服务模拟、Node.js 测试及前端渲染检查 |

PDF 文件保存在磁盘，元数据、页文本、文本块、向量、报告、记忆和初稿等保存在 MySQL。当前向量检索由 Python 计算，没有独立向量数据库，也没有神经网络 Cross-Encoder 重排序。

Redis 不是正式结果库。问答先尝试完全匹配，再按实体、意图和来源有效性等条件尝试语义复用；语义缓存依赖 `VADD` / `VSIM`，不支持这些命令时跳过语义缓存，继续 RAG。

## 快速开始

### 1. 环境要求

- Python 3.14+ 和 uv。
- Node.js `^20.19.0 || >=22.12.0`，以及 npm。
- 已启动的 MySQL、Redis、Ollama。
- 模型生成功能需要配置可用的 DeepSeek 密钥及模型。

当前已在 macOS 本机使用。Windows 推荐 WSL2 + Ubuntu，见 [Windows 指南](docs/WINDOWS.md)；Windows/WSL 和 Linux 部署模板尚未实机验收。使用已部署服务时，客户端只需要浏览器。

### 2. 获取代码和依赖

以下命令在 macOS / Linux / WSL 终端执行：

```bash
git clone https://github.com/wenxulong/research-library.git
cd research-library
uv sync --locked
ollama pull qwen3-embedding:0.6b
```

确保 MySQL 中已有应用数据库和具有迁移、读写权限的账号。全新环境可由数据库管理员执行以下示例，替换占位密码：

```sql
CREATE DATABASE IF NOT EXISTS research_library CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'research_app'@'127.0.0.1' IDENTIFIED BY 'YOUR_PASSWORD';
GRANT ALL PRIVILEGES ON research_library.* TO 'research_app'@'127.0.0.1';
```

已有账号不需要重新创建；上述语句不会更新已有账号的密码。

### 3. 配置与初始化

项目根目录下，首次安装复制模板；已有 `.env` 时保留原值：

```bash
cp -n .env.example .env
```

编辑连接信息和密钥：

```dotenv
DATABASE_URL=mysql+pymysql://research_app:YOUR_PASSWORD@127.0.0.1:3306/research_library?charset=utf8mb4
REDIS_URL=redis://127.0.0.1:6379/0
OLLAMA_BASE_URL=http://127.0.0.1:11434
EMBEDDING_MODEL=qwen3-embedding:0.6b
DEEPSEEK_API_KEY=YOUR_DEEPSEEK_API_KEY
```

模型名、地址及其他参数以 [.env.example](.env.example) 为准。密码中的 URL 特殊字符需要编码；密钥不要放入前端 `VITE_` 配置。

```bash
uv run python -B manage.py init
```

当前迁移头为 **`0009_agent_memories`**。已有环境升级前备份数据库与上传文件，停止 API 和所有 Worker，再执行迁移；不要通过手工建表替代迁移。详见 [部署说明](docs/DEPLOYMENT.md)。

### 4. 分别启动服务

终端一，在项目根目录启动 API：

```bash
uv run python -B manage.py api
```

终端二，在项目根目录启动 Worker：

```bash
uv run python -B manage.py worker
```

终端三，启动前端开发页面：

```bash
cd frontend
npm ci
npm run dev
```

- 前端：[http://127.0.0.1:5173/app/](http://127.0.0.1:5173/app/)
- API 文档：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

API 与 Worker 使用同一个配置中的队列。Worker 应注册 `papers.run_preparation_job` 和 `reports.run_report_job`，当前启动方式为单进程 `solo`。

在项目根目录检查依赖：

```bash
uv run python -B manage.py check
```

检查包含数据库迁移、Redis、Ollama 和 Worker；DeepSeek 仅检查密钥是否配置，不代表远程连接和余额已验证。忙碌的 solo Worker 可能暂时不能响应探测。

### 5. 使用构建页面

在 `frontend/` 中执行：

```bash
npm run build
```

API 运行时访问 [http://127.0.0.1:8000/app/](http://127.0.0.1:8000/app/)，不再需要 Vite 开发服务器。后端修改需重启对应进程，前端修改需重新构建。

## 第一次使用

1. 添加论文，上传 PDF，提交后台准备并等待完成。
2. 创建研究任务，关联已准备的论文，先检索核对原文，再问答或生成分析。
3. 生成多论文对比报告时，关联至少两篇论文，并确保已保存分析实际引用的证据覆盖至少两篇论文。
4. 在作业页面核对阶段、报告审校状态和来源；需要时对单条陈述启动自主查证。
5. 从通过审校的报告提出候选问题，选择问题并生成研究计划。
6. 实际运行实验，导入 CSV，选择数据集、场景和指标，查看统计、图表与分析。
7. 填写实际实现与实验条件，生成初稿；编辑或扩写后确认保存，再下载。

详细操作见 [使用指南](docs/USER_GUIDE.md)。分析报告、对比报告、研究报告和论文初稿是不同阶段的产物。

模型调用会把相应研究材料发送到配置的模型服务，并产生 API 用量。论文解析和 OCR 在本地执行；向量计算发送到配置中的 Ollama。请使用适合交给相应服务处理的材料。

## 验证与运行边界

2026-10-29 提交前验证：**后端 744 项测试通过，前端 115 项测试通过，生产构建成功。**

在项目根目录运行后端测试：

```bash
uv run python -B -m unittest discover -s tests -t . -q
```

在 `frontend/` 运行：

```bash
npm test
npm run build
```

测试主要使用独立 SQLite、模拟模型和向量服务；部分 PDF 测试实际执行本地版面解析及 OCR。测试覆盖调用、范围、格式、存储和导出行为，不能据此声称真实模型质量、缓存命中率、成本或时延已达特定指标。共享记忆接入的验收范围见 [验收记录](docs/MEMORY_ACCEPTANCE.md)。

- 论文准备和对比报告已后台化；其他模型操作仍有同步请求。
- 停止浏览器等待不会取消后端操作；结果不明确时先查询，避免重复提交。
- Worker 中断后的旧作业需要人工恢复，不会因重启自动恢复。
- 已保存论文准备数据会被复用，升级解析代码不会自动重建旧论文。重建影响原有文本块引用，操作步骤见 [PDF 解析说明](docs/PDF_PARSING.md)。
- 当前没有完整的多用户登录与权限体系，不适合直接作为公网多用户服务发布。
- 记忆和论文向量采用当前范围内的 Python 检索，尚未做大规模索引与并发性能验收。
- OCR、复杂表格和公式仍需核对；英文扫描页有测试，中文扫描论文效果未全面验证。
- 不自动执行真实科研实验，不保证候选问题具有创新性，也不自动证明理论或实验结论。

## 项目结构

```text
research-library/
├── src/research_library/
│   ├── main.py          # FastAPI 入口
│   ├── api/             # HTTP 路由
│   ├── schemas/         # 输入、输出及结构化结果
│   ├── agents/          # 分析、审校、规划、写作与 ReAct
│   ├── tools/           # 论文与记忆工具
│   ├── memory/          # 记忆存取、检索、预算与提取
│   ├── retrieval/       # PDF、分块、向量、混合检索、RAG 与缓存
│   ├── services/        # 业务流程与导出
│   ├── repositories/    # 数据库读写
│   ├── db/              # ORM 模型、会话和迁移入口
│   ├── workers/         # Celery 配置与后台作业
│   └── core/            # 配置、模型调用、结构校验与用量日志
├── frontend/            # Vue 3 + Vite
├── tests/               # 后端自动化测试
├── migrations/          # Alembic 迁移
├── skills/              # 本地写作 Skill
├── scripts/             # 人工恢复及维护
├── docs/                # 使用、部署与验收说明
├── deploy/              # 部署模板
├── manage.py            # init / check / api / worker
├── pyproject.toml
├── uv.lock
├── .env.example
└── LICENSE
```

典型请求：`前端 → api → services / agents → repositories → MySQL`；后台请求增加 `Redis → Worker`，Agent 调用工具时再进入检索或记忆模块。简单接口允许直接调用 Repository，不强制增加转发层。

## 文档

- [使用指南](docs/USER_GUIDE.md)
- [目录与架构](docs/ARCHITECTURE.md)
- [配置、迁移、部署与作业恢复](docs/DEPLOYMENT.md)
- [Windows / WSL2](docs/WINDOWS.md)
- [PDF 版面解析与 OCR](docs/PDF_PARSING.md)
- [共享记忆验收](docs/MEMORY_ACCEPTANCE.md)
- [前端说明](frontend/README.md)

## 许可证

项目原创代码采用 **AGPL-3.0-only**，完整条款见 [LICENSE](LICENSE)。第三方依赖、模型和论文资料保留各自的许可与权利。

密钥、数据库密码、上传论文、运行数据、日志及本机构建产物不随源码发布，忽略规则见 [.gitignore](.gitignore)。
