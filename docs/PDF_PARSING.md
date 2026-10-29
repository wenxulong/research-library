# PDF 解析与分块

项目使用匹配版本的 PyMuPDF / PyMuPDF4LLM 1.28.2，以及 RapidOCR 3.9.1。
它们已记录在 pyproject.toml 和 uv.lock；在项目根目录执行 `uv sync --locked` 安装。
版面与 OCR 在本机执行，不会为解析而调用 DeepSeek。扫描页 OCR 使用安装包提供的本地模型；当前项目无需额外安装 Tesseract。

## 处理流程

1. `retrieval/pdf_parser.py` 输出保留原始页码的逐页 Markdown，维持原有 ExtractedPDF / ExtractedPage 接口。
2. PyMuPDF4LLM 识别阅读顺序和表格，`use_ocr=True` 允许对需要的页面执行 OCR，不强制重识别普通文字页。
3. `retrieval/text_chunker.py` 分别处理正文和 Markdown 管道表格。
   - 正文默认最长500字符、重叠100字符，优先选择段落、句末边界；没有合适边界时按字符切分。
   - 表格默认每块最多2000字符，按完整行分组，每块重复表头。紧邻且以 Table 数字 / 表 数字开头的标题也随表头保留。
   - 表格不使用字符重叠；若表头或单行超过表格限制，明确报错，不截断数据行。`chunk_pages(table_chunk_size=...)` 可调整限制。
   - 字符上限不是 Embedding 模型的 Token 上限；项目 `truncate=False`，模型超限时需调整块长，不能静默丢弃文本。
4. 页面、文本块和向量仍通过原有服务与数据库存储；无需数据库迁移。

## 重新处理旧论文

修改解析函数不会改变数据库中已经保存的页面和文本块，后台准备会复用已有数据。
先选择独立测试论文，通过 `/docs` 依次调用：

1. `POST /papers/{paper_id}/document/extract`：重新提取页面；现有 replace_document_pages 会删除该文档旧文本块。
2. `POST /papers/{paper_id}/chunks`：生成新文本块。
3. `POST /papers/{paper_id}/chunks/embeddings`：重新生成向量。

重建可能改变块ID，不对已有正式报告引用的论文批量执行；本次开发未重建业务论文，也未改业务数据库。
代码更新后需在合适时机重启API和Worker，运行中的进程不会自动加载新实现。

## 验证

```bash
.venv/bin/python -B -m unittest tests.services.testpdf_parser tests.services.test_pdf_layout tests.services.test_text_chunker tests.services.test_structured_chunking -v
```

`test_pdf_layout` 在临时目录创建真实双栏、表格、纯图片和空白页面，实际执行版面分析及英文OCR，检查原始页码、方法/数值关联和解析后分块。测试不调用大模型、不连接正式数据库。

## 本轮边界

- 未自动合并跨页正文或跨页表格，仍按原始页码保存；邻近块工具可补充跨页上下文。
- 不能保证复杂合并单元格、图片表格、数学公式全部正确。纯图片英文正文已验证，中文扫描论文和复杂表格仍应按实际文件检查。
- 表格只按识别出的 Markdown 行处理，不自行猜测或修复错误数字；OCR后需要对照PDF核实关键实验数据。
- 保留解析器识别出的页眉页脚，避免把短页上的唯一正文误删；不以全局正则猜测删除重复内容。
- 没有引入向量语义分块，也没有修改现有Agent、RAG、引用和数据库结构。
