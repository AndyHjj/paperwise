# 将双语 PDF 生成功能加入 CLI

**日期**: 2026-07-06

## 改动文件

### `research_helper/cli.py`

在 `rh translate` 命令中新增两个选项：

- `--bilingual`：标记，加此标志时生成中英双语对照 PDF（而非默认的 Markdown）
- `--max-pages`：可选，限制双语 PDF 的页数（默认全部页面）

#### 具体变更

| 范围 | 变更 |
|---|---|
| 文件顶部 docstring | 新增 `rh translate --bilingual` 使用示例 |
| 命令装饰器 | 新增 `--bilingual` 和 `--max-pages` 两个 `@click.option` |
| `translate()` 函数签名 | 新增 `bilingual: bool` 和 `max_pages: int \| None` 参数 |
| 函数 docstring | 更新为 "Translate a paper into Chinese Markdown or generate a bilingual PDF." |
| 函数体 `with Progress` 内 | 新增 `if bilingual:` 分支，调用 `pdf_translator.generate()` 生成双语 PDF；原有逻辑移至 `else` 分支 |
| 输出信息 | 根据 `bilingual` 显示 "Bilingual PDF" 或 "Translation" |
| `flush_to_log` 标签 | 双语 PDF 使用 `translate-pdf:` 前缀，Markdown 翻译使用 `translate:` 前缀 |
| `--jobs` 和 `--max-section-chars` 的 help 文本 | 追加 `(Markdown only)` 说明 |

## 使用方式

```bash
# 生成中文 Markdown 翻译（原有行为，不变）
rh translate --arxiv 2401.12345

# 生成双语 PDF（新增）
rh translate --arxiv 2401.12345 --bilingual

# 双语 PDF + 只翻译前 10 页
rh translate --arxiv 2401.12345 --bilingual --max-pages 10

# 本地 PDF 生成双语 PDF
rh translate --pdf paper.pdf --bilingual
```
