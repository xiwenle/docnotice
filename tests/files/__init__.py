"""tests/files —— 真实测试文件生成器包。

- generators.py：PDF/DOCX/XLSX/XLS/PPTX/大文件/损坏/重复/伪造扩展名 生成器 + sha256 工具
- 用法：from files.generators import make_pdf, PDF_FILENAME, sha256_of
  （tests/ 目录在 sys.path 中，files 是其子包）

fixture 约定：测试模块内用 tmp_path 生成文件，例如
    pdf = make_pdf(tmp_path / PDF_FILENAME)
文件名常量见 generators.py（均为中文文件名，§32）。
"""
from files.generators import (  # noqa: F401
    DOCX_FILENAME,
    LARGE_FILENAME,
    PDF_FILENAME,
    PPTX_FILENAME,
    XLS_FILENAME,
    XLSX_FILENAME,
    make_corrupted,
    make_docx,
    make_duplicate,
    make_fake_pptx,
    make_large_pdf,
    make_large_xlsx,
    make_pdf,
    make_pptx,
    make_xls,
    make_xlsx,
    sha256_of,
    unique_name,
    write_raw_file,
)