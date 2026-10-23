# Windows 使用指南：WSL2 + Ubuntu

本项目当前已在macOS验收；**Windows推荐使用WSL2运行，尚未完成Windows/WSL实机验收**。本文命令根据现有项目入口与官方安装文档整理，不表示已在Windows执行通过。

Windows用户在Edge或Chrome操作网页，API、Celery Worker、MySQL、Redis和Ollama统一运行在WSL2的Ubuntu中。Celery官方不支持原生Windows，因此本指南不以Windows原生Worker作为完整运行方案。[Celery官方说明](https://docs.celeryq.dev/en/stable/getting-started/introduction.html)

如果项目将来部署到服务器，Windows用户只需访问服务器网页，无需在本机安装下列开发环境。

## 1. 安装WSL2（Windows PowerShell）

适用于支持WSL2的Windows 10/11。以管理员身份打开PowerShell：

```powershell
wsl --install -d Ubuntu
```

根据提示重启，打开Ubuntu，设置Linux用户名和密码。密码输入时终端不显示字符，这是正常行为。

在PowerShell检查发行版：

```powershell
wsl --list --verbose
```

Ubuntu的VERSION应为2；如果是1，按列表中的实际发行版名称转换：

```powershell
wsl --set-version Ubuntu 2
```

参考[微软WSL安装说明](https://learn.microsoft.com/en-us/windows/wsl/install)。**从下一节开始，除非明确标注PowerShell，命令全部在Ubuntu终端执行。**

## 2. 安装基础工具、MySQL和Redis（Ubuntu）

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates build-essential zstd mysql-server redis-server fonts-noto-cjk
sudo service mysql start
sudo service redis-server start
redis-cli ping
```

最后应输出PONG。fonts-noto-cjk用于中文图表；Ubuntu内安装的字体与Windows字体不是同一份。

这里的Ubuntu仓库Redis可用于队列与完全匹配缓存，但不保证支持语义缓存所需的向量命令，见第10节。不要因为能PING成功就认定语义缓存已就绪。

## 3. 安装uv、Python和Node.js（Ubuntu）

### uv与Python

按[uv官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)安装到Ubuntu：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
uv --version
uv python install 3.14
```

如果提示env文件不存在，按安装器输出的路径加载环境，或重新打开Ubuntu终端。Python由uv独立管理，无需修改Ubuntu系统Python。

### Node.js

在Ubuntu内安装Node，不复用Windows的node.exe。以下使用[nvm官方安装说明](https://github.com/nvm-sh/nvm#installing-and-updating)中的版本管理器：

```bash
curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.8/install.sh | bash
source "$HOME/.nvm/nvm.sh"
nvm install 22
nvm alias default 22
node --version
npm --version
which node
```

项目要求见frontend/package.json，Node 22需要至少22.12。which node应显示Linux路径，例如/home/.../.nvm/...，不能指向/mnt/c/...中的Windows安装。

## 4. 在Ubuntu主目录下载项目

```bash
cd ~
git clone https://github.com/wenxulong/research-library.git
cd ~/research-library
uv sync --locked --python 3.14
```

建议将项目放在~/research-library，不要直接复制Mac上的.venv或node_modules。依赖需要在Ubuntu重新安装；uv.lock和package-lock.json用于锁定版本。

已有克隆请进入原目录更新，无需重复克隆。

## 5. 创建数据库并配置.env

首次安装，在Ubuntu终端进入MySQL：

```bash
sudo mysql
```

在MySQL提示符执行以下SQL，将YOUR_PASSWORD替换为自己的应用密码：

```sql
CREATE DATABASE IF NOT EXISTS research_library CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'research_app'@'127.0.0.1' IDENTIFIED BY 'YOUR_PASSWORD';
GRANT ALL PRIVILEGES ON research_library.* TO 'research_app'@'127.0.0.1';
EXIT;
```

这里只授权项目数据库。已有账号不会被CREATE USER IF NOT EXISTS重设密码；已有数据库可直接使用，不要删除重建。

回到项目根目录：

```bash
cd ~/research-library
cp -n .env.example .env
nano .env
```

cp -n不会覆盖已有.env。nano中编辑后按Ctrl+O、回车保存，再按Ctrl+X退出。至少核对：

```dotenv
DATABASE_URL=mysql+pymysql://research_app:YOUR_PASSWORD@127.0.0.1:3306/research_library?charset=utf8mb4
REDIS_URL=redis://127.0.0.1:6379/0
OLLAMA_BASE_URL=http://127.0.0.1:11434
EMBEDDING_MODEL=qwen3-embedding:0.6b
DEEPSEEK_API_KEY=YOUR_DEEPSEEK_API_KEY
API_HOST=127.0.0.1
API_PORT=8000
```

使用自己的密码和密钥，其他配置保留模板值并按实际服务核对。数据库密码中的@、#等URL特殊字符需要编码。不要把密钥写进frontend的VITE_配置，也不要提交.env。

上面127.0.0.1成立的前提是依赖都在同一个Ubuntu环境中。不要将Windows里的MySQL或Ollama与此配置混用；跨系统连接见第11节。

## 6. 安装Ollama并下载向量模型

在Ubuntu按[Ollama Linux官方说明](https://docs.ollama.com/linux)安装：

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

如果安装后服务未运行，在一个单独的Ubuntu终端执行，并保持该终端打开：

```bash
ollama serve
```

若提示11434端口已占用，先检查已有Ollama服务，不要重复启动。在另一个Ubuntu终端下载模型：

```bash
ollama pull qwen3-embedding:0.6b
ollama list
curl -fsS http://127.0.0.1:11434/api/tags
```

模型下载需要网络、磁盘和内存。GPU不是本项目的必选条件，CPU运行的速度取决于机器。Linux安装器的硬件提示不代表整个项目要求必须有GPU。

## 7. 初始化数据库并构建前端

首次安装：

```bash
cd ~/research-library
uv run --locked python -B manage.py init
cd frontend
npm ci
npm run build
```

数据库初始化通过Alembic执行，当前迁移头为0008_investigation_adoption。旧数据库升级前先备份并停止API和全部Worker，见[部署文档](DEPLOYMENT.md)。

uv run让命令使用项目虚拟环境，不需要手动激活，也避免混用Windows的.venv\Scripts和Linux的.venv/bin。

## 8. 启动服务，在Windows浏览器使用

### Ubuntu终端一：API

```bash
cd ~/research-library
uv run --locked python -B manage.py api
```

### Ubuntu终端二：Worker

```bash
cd ~/research-library
uv run --locked python -B manage.py worker
```

保持两个终端打开；Ollama如果使用手动serve，也要保持对应终端打开。项目默认Worker使用solo模式。

### Ubuntu终端三：检查

```bash
cd ~/research-library
uv run --locked python -B manage.py check
curl -fsS http://127.0.0.1:8000/health/live
```

check核对数据库、迁移、Redis、Ollama和Worker；密钥配置存在不代表远程模型调用一定成功。solo Worker正忙时可能未确认，需结合日志和作业阶段判断。

随后在**Windows的Edge或Chrome**打开：

- 页面：[http://localhost:8000/app/](http://localhost:8000/app/)
- 接口文档：[http://localhost:8000/docs](http://localhost:8000/docs)

微软说明Windows通常可以通过localhost访问WSL中的网页服务，见[WSL网络说明](https://learn.microsoft.com/en-us/windows/wsl/networking)。不需要为了本机使用把API绑定改成0.0.0.0。

需要开发前端时，在另一个Ubuntu终端运行：

```bash
cd ~/research-library/frontend
npm run dev
```

然后访问http://localhost:5173/app/。开发模式和8000的构建页面访问相同后端，但浏览器记忆按站点隔离。

完整使用流程见[七阶段使用指南](USER_GUIDE.md)。

## 9. 再次启动、停止和验收

重启Windows或关闭WSL后，先检查依赖是否仍运行，必要时执行：

```bash
sudo service mysql start
sudo service redis-server start
```

Ollama有服务则启动其服务；没有服务则独立终端运行ollama serve。再按第8节启动API和Worker，不必每次重新安装、下载模型或初始化数据库。

在各运行终端按Ctrl+C可停止API、Worker或手动Ollama。Worker有作业时应等待完成，不要强行关闭WSL。

### 自动化检查

```bash
cd ~/research-library
MPLCONFIGDIR=/tmp/research-matplotlib-cache uv run --locked python -B -m unittest discover -s tests -t . -q
cd frontend
npm test
npm run build
```

这些测试不调用真实模型，也不应连接正式数据库做删表测试。通过后，再使用独立演示记录验收PDF准备、问答、作业、方案、实验导入、初稿扩写保存和下载；真实模型验收会产生用量并发送所用研究材料。

请在完成真实Windows验收前保留本文“尚未实机验收”的标注。

## 10. 语义缓存的额外条件

在Ubuntu执行只读能力检查：

```bash
redis-cli COMMAND INFO VADD VSIM
```

两条命令都应返回定义，而不是nil。PING成功只证明可连接，不能证明支持向量命令。

如果Ubuntu仓库版本不支持这些命令，可按[Redis官方APT安装说明](https://redis.io/docs/latest/operate/oss_and_stack/install/install-stack/apt/)选择支持向量集合的发行版本和配置；升级前应停止API和Worker，并按已有数据需求备份Redis。不要另外启动第二个服务占用6379。

项目使用[VADD/VSIM向量集合](https://redis.io/docs/latest/develop/data-types/vector-sets/)，不是FT.SEARCH向量索引。不支持时跳过语义缓存并继续RAG；队列、数据库和其他功能不会因此自动获得或丢失向量能力。

## 11. 常见问题

### Windows浏览器打不开

先在Ubuntu执行第8节的curl检查API。Ubuntu内也不通，先查看API终端是否有错误；Ubuntu内能访问但Windows不通，再按微软网络说明检查WSL和localhost转发。

### 连接Windows版MySQL或Ollama失败

默认WSL NAT模式下，Ubuntu中的127.0.0.1指Ubuntu本身，不能直接当成Windows主机地址。推荐把服务放到Ubuntu；必须混用时，按[微软网络说明](https://learn.microsoft.com/en-us/windows/wsl/networking)配置主机地址或适用的镜像网络，并调整对应.env地址。不要直接开放数据库到公网。

### 找不到uv、node或npm

确认当前是Ubuntu终端，在Ubuntu安装并加载相应环境。which node应是Linux路径；不要复用Windows的node.exe，也不要在PowerShell运行Linux的source命令。

### 图表中文变成方框

确认Ubuntu内已安装fonts-noto-cjk，然后重启API。如果曾在字体安装前生成Matplotlib缓存，按Matplotlib缓存位置清理对应字体缓存后重试；仅在Windows安装字体不会自动让WSL里的绘图库看到它。

### 想迁移Mac上的现有资料

克隆GitHub只得到代码，不包含.env、数据库、上传PDF和已保存的报告/初稿。需要另行备份导入数据库并复制PDF；旧记录的file_path可能是Mac绝对路径，须映射为WSL中的实际路径。只改UPLOAD_DIR不会修复旧路径。

首次Windows验证建议先创建全新独立数据，确认可用后再迁移已有资料，不要直接覆盖原库。
