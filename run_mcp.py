#!/usr/bin/env python3
"""gx3gen MCP stdio 启动器（仓库根入口）。

用法::

    python run_mcp.py

把 ``<repo>/src`` 插入 ``sys.path``，再启动 ``gx3gen.mcp_server``：
MCP 2024-11-05 / JSON-RPC 2.0，stdio 按行分隔。
stdout 只输出协议 JSON，日志与异常全部写 stderr。

MCP 客户端（Claude Desktop / DSH / Cursor 等）配置见仓库根
``mcp.json.example`` 与 ``docs/AGENT_WORKFLOW.md``。
"""
from __future__ import annotations

import os
import sys


def _repo_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    repo_root = _repo_root()
    src_dir = os.path.join(repo_root, "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    # 允许从任意工作目录启动：运行期数据默认仍落在 <repo>/workspace（GX3GEN_WORKSPACE 可覆盖）
    try:
        from gx3gen.mcp_server import main as server_main
    except Exception as exc:  # 导入失败也只写 stderr，避免污染协议通道
        sys.stderr.write(
            "[gx3gen-mcp] 无法导入 gx3gen.mcp_server（检查 Python 版本 >= 3.9 与 src/ 目录）: %s\n"
            % exc
        )
        return 1
    return int(server_main() or 0)


if __name__ == "__main__":
    sys.exit(main())
