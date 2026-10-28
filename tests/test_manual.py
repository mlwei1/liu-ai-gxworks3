"""手册知识库：提取 / 切块 / SQLite FTS5 检索 / 目录 / 阅读 / 删除。"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


SAMPLE = """# FX5 测试手册

## SET / RST 指令

SET 指令只接受 1 个操作数，RST 指令也只接受 1 个操作数。
多个设备必须拆成多条指令，否则 GX Works3 导入会报「指令不正确」。

## STL 步进指令

STL 步块需要 RETSTL 结束，每个 STL 步块配一个 RETSTL。

## 高速计数器

FX5U 的高速计数器最大频率与 CPU 型号、计数器模式有关。
请按硬件手册确认输入滤波和响应频率。
"""


class ManualTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gx3gen-manual-test-")
        self.old = os.environ.get("GX3GEN_WORKSPACE")
        os.environ["GX3GEN_WORKSPACE"] = self.tmp
        self.md = Path(self.tmp) / "test_manual.md"
        self.md.write_text(SAMPLE, encoding="utf-8")

    def tearDown(self):
        if self.old is None:
            os.environ.pop("GX3GEN_WORKSPACE", None)
        else:
            os.environ["GX3GEN_WORKSPACE"] = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_list_search_toc_read_remove(self):
        from gx3gen.manual import add_manual, list_manuals, read, remove_manual, search, toc

        result = add_manual(self.md, title="FX5 测试手册", model="FX5U")
        self.assertTrue(result["ok"])
        self.assertGreater(result["sections"], 1)
        self.assertGreater(result["chunks"], 0)
        manual_id = result["manual_id"]

        manuals = list_manuals()
        self.assertEqual(len(manuals), 1)
        self.assertEqual(manuals[0]["id"], manual_id)
        self.assertEqual(manuals[0]["model"], "FX5U")

        hits = search("SET 操作数", top_k=5)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["manual_id"], manual_id)
        self.assertIn("SET", hits[0]["snippet"])
        self.assertIn("p.", hits[0]["citation"])
        self.assertIn("SET / RST", hits[0]["heading_path"])

        items = toc(manual_id, depth=2)
        self.assertTrue(items)
        self.assertTrue(any("SET / RST" in item["heading"] for item in items))

        section_id = hits[0]["section_id"]
        content = read(manual_id, section_id=section_id)
        self.assertIn("SET", content["text"])
        self.assertIn("操作数", content["text"])

        removed = remove_manual(manual_id)
        self.assertTrue(removed["ok"])
        self.assertEqual(list_manuals(), [])
        self.assertEqual(search("SET"), [])

    def test_duplicate_sha_is_skipped(self):
        from gx3gen.manual import add_manual

        first = add_manual(self.md, title="FX5 测试手册")
        second = add_manual(self.md, title="FX5 测试手册")
        self.assertTrue(second["skipped"])
        self.assertEqual(first["manual_id"], second["manual"]["id"])

    def test_chinese_query(self):
        from gx3gen.manual import add_manual, search

        add_manual(self.md, title="FX5 测试手册")
        hits = search("高速计数器 最大频率")
        self.assertTrue(hits)
        self.assertIn("高速计数器", hits[0]["snippet"] + hits[0]["heading_path"])

    def test_split_text_overlap(self):
        from gx3gen.manual.chunk import split_text

        text = "\n\n".join(["段落%d" % i + "内容" * 200 for i in range(6)])
        chunks = split_text(text, target_chars=600, overlap_chars=100)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.strip() for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
