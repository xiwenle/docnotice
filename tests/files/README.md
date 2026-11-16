# tests/files/ —— 真实测试文件生成器

> 目标：**真实可解析**的测试文件（项目规范 §32），不是伪造字节。
> 依赖：pypdf / python-docx / openpyxl / xlwt（均在 requirements.txt 或 requirements-dev.txt）。

## 生成器清单（generators.py）

| 函数 | 产物 | 中文文件名常量 | 可解析性验证 |
|---|---|---|---|
| `make_pdf(path, *, title, pages, body)` | 真实 PDF（页面文本可提取、元数据 UTF-16 中文标题） | `PDF_FILENAME = "员工手册2026.pdf"` | `pypdf.PdfReader`：页数/`extract_text()` |
| `make_docx(path, *, title, paragraphs, table_rows, table_cols)` | 真实 DOCX（中文标题/段落 + 表格） | `DOCX_FILENAME = "公司制度.docx"` | `python-docx`：段落文本/表格单元格 |
| `make_xlsx(path, *, sheet_names, rows, cols, values)` | 真实 XLSX（多工作表、中文数据） | `XLSX_FILENAME = "费用报销表.xlsx"` | `openpyxl.load_workbook`：sheet/max_row/单元格 |
| `make_xls(path, *, sheet_names, rows, cols)` | 真实 XLS（BIFF8/OLE2，xlwt 写） | `XLS_FILENAME = "工资明细表.xls"` | `xlrd.open_workbook`：sheet/单元格 |
| `make_large_pdf(path, target_bytes)` | 大小**可配置**的真实 PDF（误差 <2KB，仍可解析） | `LARGE_FILENAME` | PdfReader 可读 |
| `make_large_xlsx(path, target_bytes)` | 大小可配置的真实 XLSX（按行数估算） | — | openpyxl 可读 |
| `make_corrupted(path, src)` | 损坏文件：保留魔数头 + 中段翻转 + 截断 → 魔数校验能过、解析必然失败 | — | 解析器抛异常 |
| `make_duplicate(src, dst)` | 同一内容副本（重复文件测试，SHA-256 相同） | — | `sha256_of` 相等 |
| `write_raw_file(path, content)` | 任意字节（伪造扩展名测试：exe 内容改名 .pdf） | — | 魔数校验应拒绝 |
| `sha256_of(path)` | SHA-256（与 `file_safety.compute_sha256` 同口径） | — | 长度 64 hex |
| `unique_name(ext)` | uuid4.hex + ext（与内部存储名同形态） | — | — |

## 魔数契约（DESIGN.md §5.1）

- PDF：`%PDF-`（前 5 字节）
- DOCX/XLSX：`PK\x03\x04`（ZIP 容器）
- XLS：`D0 CF 11 E0 A1 B1 1A E1`（OLE2 复合文档）

生成器输出天然满足上述魔数 → 上传管线（扩展名白名单→魔数→大小→清洗）可直接复用。

## 用法示例

```python
from files.generators import make_pdf, XLSX_FILENAME, sha256_of

pdf = make_pdf(tmp_path / PDF_FILENAME, pages=3)      # 中文文件名
xlsx = make_xlsx(tmp_path / XLSX_FILENAME, rows=20)
assert sha256_of(pdf) == sha256_of(make_duplicate(pdf, tmp_path / "副本.pdf"))