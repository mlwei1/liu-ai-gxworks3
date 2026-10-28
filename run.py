"""仓库根启动器：把 src 加入 sys.path 后调用 gx3gen.cli。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from gx3gen.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
