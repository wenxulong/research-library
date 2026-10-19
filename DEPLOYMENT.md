# 后端配置、运行和单机部署

## 已验证范围

Mac本机使用已有MySQL、Redis、Ollama，API与单进程Celery Worker分开运行。Linux systemd文件是部署模板，未在Linux服务器实际安装验收。

## 1. 依赖和配置

```bash
cd /path/to/research-library
uv sync --locked
```

新环境且没有`.env`时，复制`.env.example`为`.env`并填写实际连接和密钥。已有`.env`保留原值，只按需增加可选项。模板中没有真实密码或密钥。

配置统一从`config.py`读取：系统环境变量优先，项目根目录`.env`补充缺失变量。相对上传/日志路径都相对项目目录，与终端当前目录无关。修改配置后重启API与Worker；DeepSeek密钥在模型调用时检查，未设置不阻止数据库维护或论文准备。

| 配置 | 含义/默认值 |
|---|---|
| DATABASE_URL | 现有MySQL连接，数据库须预先存在 |
| REDIS_URL | Celery消息队列连接 |
| DEEPSEEK_API_KEY | 仅模型调用需要 |
| CELERY_QUEUE | report_jobs；API投递与启动Worker保持一致 |
| OLLAMA_BASE_URL | http://127.0.0.1:11434 |
| EMBEDDING_MODEL | qwen3-embedding:0.6b |
| EMBEDDING_TIMEOUT | 单次向量HTTP请求超时120秒 |
| DEEPSEEK_BASE_URL / DEEPSEEK_MODEL | 保留项目现有服务与模型配置 |
| DEEPSEEK_TIMEOUT | 单次SDK请求超时300秒，不是整个作业的时间上限 |
| DEEPSEEK_MAX_RETRIES | SDK请求重试2次，区别于应用的结构化回答重试 |
| DEEPSEEK_JSON_MAX_TOKENS | JSON回答输出上限8192 |
| UPLOAD_DIR / MAX_UPLOAD_MB | uploads/papers，20MB |
| API_HOST / API_PORT | 127.0.0.1，8000 |
| LOG_LEVEL / LOG_DIR | info，logs |
| HEALTH_TIMEOUT | 依赖探测网络操作的超时2秒，不保证所有探测合计只用2秒 |

路径改动只影响新文件的保存位置，不自动搬迁已有PDF。复制既有数据库到新机器时，必须同时保留PDF文件并处理数据库中已保存的绝对file_path，不能只复制数据库。

## 2. 启动顺序

1. 启动预先安装的MySQL、Redis和Ollama；确认Ollama已安装配置中的向量模型。
2. 部署结构迁移时先备份数据库和uploads，停止API及全部Worker。
3. 执行统一迁移：

```bash
.venv/bin/python -B manage.py init
```

4. 在第一个终端启动API：

```bash
.venv/bin/python -B manage.py api
```

5. 在第二个终端启动Worker：

```bash
.venv/bin/python -B manage.py worker
```

6. 在第三个终端检查依赖：

```bash
.venv/bin/python -B manage.py check
```

`check`退出码0表示全部探测通过，1表示异常或未确认。它直接检查依赖，并不证明API端口在监听；API存活由`/health/live`确认。Worker尚未启动时，check不会通过。

## 3. 健康检查

- `GET /health/live`：API能响应就返回200，不访问外部依赖。可用于API进程存活检查。
- `GET /health/ready`：检查MySQL及迁移版本、Redis、Ollama模型清单、同一个Worker是否注册两类任务并消费配置队列。全部确认可用返回200，否则503。
- `status=error`是已发现异常，`unknown`是无法确认。尤其solo Worker执行长任务时会阻塞控制命令，超时不能证明Worker已死；不要因为一次ready失败自动强杀Worker或启动额外消费者。
- `capabilities.deepseek_configured`仅表示密钥存在，不验证密钥有效性、余额、远程模型可用性，也不调用DeepSeek。
- 检查只返回状态和错误类型，不返回数据库/Redis连接串、密钥或异常原文。没有执行新的论文/报告作业。

Celery对solo Worker远程控制的限制见[官方文档](https://docs.celeryq.dev/en/stable/userguide/workers.html#remote-control)。

## 4. 日志与停止

- `logs/api.log`：API与访问日志，同时输出终端；10MB轮转，保留3份旧日志。
- `logs/worker.log`：Worker启动、任务和错误日志；长期运行需要定期归档，Linux可配置logrotate。
- 两个前台终端各按一次Ctrl+C，等待进程退出。Worker会等待正在执行的任务结束；不要连续按多次强行终止任务。
- systemd部署用`systemctl stop`发送SIGTERM；模板不因默认停止超时强杀长任务。

确认Worker中断后：停止API与本项目全部Worker，执行对应恢复脚本，再启动服务并创建新作业。恢复过程为人工流程，服务进程自动重启不代表数据库里旧running作业自动恢复。

## 5. Linux systemd模板

部署目录默认`/opt/research_library`，运行用户/组默认`research_library`。管理员应先创建该用户、赋予项目目录及uploads/logs读写权限、安装兼容项目Python版本的运行环境，并在该目录同步依赖和配置`.env`。按实际机器修改`deploy/systemd`的用户、组、路径。

先迁移并检查，再将两个service模板安装到`/etc/systemd/system/`，执行：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now research-library-api research-library-worker
sudo systemctl status research-library-api research-library-worker
```

MySQL、Redis、Ollama作为外部前置服务单独部署；模板不会安装它们或修改它们的系统配置。生产服务应通过systemd等进程管理工具运行，[Celery官方建议](https://docs.celeryq.dev/en/stable/userguide/workers.html#restarting-the-worker)。

默认API绑定127.0.0.1，适合本机使用或由反向代理访问。当前项目没有多用户登录与隔离，公网多用户部署属于后续阶段。

## 6. 更新和验收

- 停止接收新的业务请求；等待已提交的作业结束，停止API和Worker。
- 备份数据库、PDF文件和配置，更新代码并执行`uv sync --locked`。
- 用`manage.py init`应用迁移，重启API/Worker，检查live和ready。
- 用独立测试论文提交准备作业，查询状态与结果；报告验收涉及远程模型时另行确认数据发送范围。
- 运行自动化测试：

```bash
.venv/bin/python -B -m unittest discover -q
```

测试使用独立SQLite及服务模拟，不能改成正式数据库进行删表测试。

## Vue前端部署

在frontend目录执行npm ci和npm run build，再重启API。FastAPI从frontend/dist提供/app页面及/app/assets静态资源。构建页面同源调用后端，无需在前端保存密钥。发布需同时更新index.html和对应的哈希资源；入口禁用缓存避免旧页面引用已移除资源。开发用5173，构建预览用4173。

## 数据一致性与页面等待

报告排队或执行期间，关联论文的删除、正文重建、分块及向量重建、论文准备提交会被拒绝；关联论文集合也不能改变。所有关联论文都受保护，因为补证可能检索到最初未引用的论文。报告结束后解除这些限制。

关联变化后，已有分析标记为 stale（过期），旧正文保留在任务结果中，但不能再用于当前审校、版本恢复或新报告；重新分析后恢复 completed。审校及重新分析保存前会比较输入版本，输入变化返回409，不覆盖最新结果。已完成报告仍作为历史快照展示。

普通前端修改请求最多等待30秒，读取最多15秒；同步模型请求最多等待15分钟，；部分页面提供“停止等待”，实验与写作页面没有主动取消后端执行的功能。停止等待/超时只结束浏览器请求，不取消后端执行；操作结果未确认时应先刷新或查询状态，避免重复提交。


## 当前数据库和写作功能

当前迁移头为`0006_paper_drafts`。从旧版本升级时必须带齐0003～0006迁移源码；不要只复制model.py或手工新建部分表。

论文初稿正文和材料快照保存在MySQL的paper_drafts表，备份时包含该表及experiment_imports、research_question_sets。Redis缓存丢失不影响这些已保存结果。

研究方案、实验分析、初稿生成和章节扩写仍为同步API请求，不经Celery。章节扩写只生成建议；用户确认后PATCH保存；GET读取及导出不调用模型。反向代理的读取超时应适应同步模型请求，前端超时不代表服务器取消执行。

问答语义缓存使用Redis的VADD/VSIM命令。仅有普通队列连接不代表支持向量命令；不支持时会跳过语义缓存。此特性不改变MySQL持久化要求。
