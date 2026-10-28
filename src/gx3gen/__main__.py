"""`python -m gx3gen` 入口（需 PYTHONPATH=src 或从 run.py 启动）。"""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
