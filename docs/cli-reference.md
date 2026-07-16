# Paperwise CLI 功能与命令

本文档对应当前 `feat/layout-preserving-bilingual-pdf` 分支。命令行入口为
`rh`，由 `research_helper.cli:main` 提供。

## 安装与配置

在项目根目录执行：

```powershell
python -m pip install -e .
Copy-Item .env.example .env
```

随后在 `.env` 中选择 LLM 提供商、模型并填写对应 API Key。当前支持：

| 提供商 | `LLM_PROVIDER` | 必需的 Key |
|---|---|---|
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` |
| OpenAI | `openai` | `OPENAI_API_KEY` |
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` |
| Qwen | `qwen` | `QWEN_API_KEY` |
| MiMo | `mimo` | `MIMO_API_KEY` |

用以下命令确认安装结果：

```powershell
rh --help
```

`outputs/` 当前按运行命令时的工作目录解析。为避免成果分散，建议始终从同一
工作目录运行 Paperwise。

## 命令总览

| 命令 | 功能 |
|---|---|
| `rh read` | 精读单篇论文，生成中文结构化报告并默认加入知识库 |
| `rh translate` | 生成中文 Markdown 译文或保留布局的中英双栏 PDF |
| `rh survey` | 从 Arxiv 检索论文并生成领域综述 |
| `rh kb list` | 列出知识库中的论文 |
| `rh kb search` | 对知识库执行语义检索 |
| `rh kb stats` | 显示知识库统计信息 |
| `rh graph` | 从已有论文构建知识图谱 HTML 与 JSON |
| `rh cost` | 查看 API Token 与费用历史 |

当前没有顶层 `rh search`、`rh ask` 或 `rh kb build` 命令。论文会在执行
`rh read` 后自动加入知识库。

## `rh read`：单篇论文精读

从 Arxiv ID 或 URL 读取论文：

```powershell
rh read --arxiv 1706.03762
rh read --arxiv https://arxiv.org/abs/1706.03762
```

读取本地 PDF：

```powershell
rh read --pdf C:\papers\attention.pdf
```

参数：

| 参数 | 说明 |
|---|---|
| `--pdf PATH` | 本地 PDF 路径，与 `--arxiv` 二选一 |
| `--arxiv TEXT` | Arxiv ID 或完整 URL，与 `--pdf` 二选一 |
| `--force` | 忽略已有结果并重新生成报告 |
| `--no-kb` | 生成报告后不写入知识库 |

主要输出包括论文 PDF、`meta.json` 和 `report.md`。默认还会将论文内容写入
`outputs/.kb/`。

## `rh translate`：论文翻译

### 中文 Markdown

```powershell
rh translate --pdf C:\papers\paper.pdf
rh translate --arxiv 2502.14802 --jobs 4
```

输出为论文目录中的 `translation.md`。

### 中英双栏 PDF

```powershell
rh translate --pdf C:\papers\paper.pdf --bilingual
rh translate --arxiv 2502.14802 --bilingual --max-pages 5
```

双栏模式左侧保留原始页，右侧放置 BabelDOC 生成并经过 Paperwise 校验的
中文页。输出文件名形如：

```text
paper_paperwise_bilingual_layout.pdf
paper_paperwise_bilingual_layout_pages_1-5.pdf
```

当前 CLI 的 `--bilingual` 路径只调用
`research_helper.reports.layout_pdf`，旧版 PDF translator 已移除。

双栏 PDF 需要单独安装 BabelDOC 0.6.3，且需选择 OpenAI、DeepSeek、Qwen
或 MiMo 等 OpenAI-compatible 提供商。完整安装、质量检查与已知限制见
[布局保真 PDF 后端说明](layout-pdf-backend.md)。

参数：

| 参数 | 说明 |
|---|---|
| `--pdf PATH` | 本地 PDF 路径，与 `--arxiv` 二选一 |
| `--arxiv TEXT` | Arxiv ID 或完整 URL，与 `--pdf` 二选一 |
| `--force` | 忽略可复用缓存并重新生成 |
| `--jobs INTEGER` | Markdown 模式并发翻译的章节数，默认 `1` |
| `--max-section-chars INTEGER` | Markdown 模式的章节切分上限，默认 `10000` |
| `--bilingual` | 输出中英双栏 PDF，而不是 Markdown |
| `--max-pages INTEGER` | 双栏模式只处理前 N 页，最小值为 `1` |

`--jobs` 和 `--max-section-chars` 只影响 Markdown 翻译；`--max-pages` 只影响
双栏 PDF。

## `rh survey`：领域综述

```powershell
rh survey --query "retrieval augmented generation"
rh survey -q "KV Cache" --max 30 --force
```

参数：

| 参数 | 说明 |
|---|---|
| `-q, --query TEXT` | 必填，Arxiv 检索主题 |
| `--max INTEGER` | 最多检索的论文数量，默认 `20` |
| `--force` | 已有综述存在时仍重新生成 |

输出目录形如 `outputs/survey_KV_Cache/`，其中包含 `survey.md` 和
`papers.json`。

## `rh kb`：本地知识库

列出已收录论文：

```powershell
rh kb list
```

语义检索：

```powershell
rh kb search "graph-based approximate nearest neighbor"
rh kb search "graph-based approximate nearest neighbor" --top 10
```

查看统计信息：

```powershell
rh kb stats
```

`rh kb search` 的位置参数 `QUERY` 为检索问题，`--top` 控制返回条数，默认
为 `5`。Embedding 提供商通过 `EMBEDDING_PROVIDER` 配置；未显式设置时，
Paperwise 会依次尝试 Qwen、OpenAI，最后使用本地模型。

## `rh graph`：知识图谱

```powershell
rh graph
rh graph --threshold 0.6 --open
rh graph --out outputs\my-graph --no-cache
```

参数：

| 参数 | 说明 |
|---|---|
| `--out TEXT` | 图文件输出目录，默认 `outputs/graph` |
| `--threshold FLOAT` | 论文相似边的余弦相似度阈值，默认 `0.55` |
| `--no-cache` | 删除各论文的 `graph_info.json` 后重新提取图信息 |
| `--open` | 完成后用默认浏览器打开 HTML |

输出包括交互式 `graph.html` 和结构化 `graph.json`。

## `rh cost`：费用记录

```powershell
rh cost
rh cost --last 30
```

`--last` 指定显示最近多少条记录，默认 `10`。费用历史保存在
`outputs/.cost_log.jsonl`，命令还会显示累计美元费用及人民币估算。

## 输出目录规则

对于可识别 Arxiv ID 的论文，新目录采用：

```text
Arxiv_ID_可读标题
```

例如：

```text
outputs/2502_14802_From_RAG_to_Memory_Non-Parametric_Continual_Learning_for_Large_Language_Models
```

目录名最长 96 个字符，并尽量在单词边界截断。无法识别 Arxiv ID 时使用
规范化后的论文标题。同一论文再次运行时会复用已存在且元数据最完整的目录，
不会产生只因 Arxiv 版本号或文件名不同而新增的输出目录。

## 常用工作流

完整处理一篇 Arxiv 论文：

```powershell
rh read --arxiv 2502.14802
rh translate --arxiv 2502.14802 --jobs 4
rh translate --arxiv 2502.14802 --bilingual
rh kb search "continual memory for language models"
rh graph --open
rh cost --last 20
```

仅检查双栏 PDF 的前两页：

```powershell
rh translate --pdf C:\papers\paper.pdf --bilingual --max-pages 2 --force
```

遇到参数问题时，可以逐级查看内置帮助：

```powershell
rh --help
rh translate --help
rh kb --help
rh kb search --help
```
