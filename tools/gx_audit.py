#!/usr/bin/env python3
"""GX Works3 CSV 导入前自检命令行入口（逻辑在 src/gx3gen/audit.py）。

用法:
    python tools/gx_audit.py <program.csv> [--check comments.csv] [--expect-title "Chinese Simplified/简体中文"]

检查项:
  1. 程序本体 CSV：BOM / 行尾 / 表头 / 列数 / END
  2. 操作数个数：SET/RST=1、OUT=1~2、ZRST=2、CMP/SUB=3、MOV=2 …
  3. IL 序列：LD/LDI 后不得紧跟另一个 LD/LDI（悬空支路 → 「梯形图配置不正确」）
  4. STL/RETSTL 配对：一个 STL 步块配一个 RETSTL
  5. 软元件注释 CSV：LF 行尾 / 标题行 / 可选的目标工程注释标题

参考：docs/GX_IMPORT_TROUBLESHOOTING.md
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gx3gen.audit import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main(sys.argv))
