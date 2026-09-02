from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("category_memory.py")
SPEC = importlib.util.spec_from_file_location("barinella_category_memory", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class CategoryMemoryTests(unittest.TestCase):
    def test_manifest_uses_category_id_as_its_only_business_key(self) -> None:
        table = MODULE.MANIFEST["migrations"][0]["operations"][0]
        self.assertEqual(table["unique"], [["category_id"]])
        self.assertEqual([column["name"] for column in table["columns"]], ["id", "category_id", "aliases", "when_to_use", "when_not_to_use", "examples", "notes"])

    def test_provisions_and_upserts_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); workspace = root / "workspace"; workspace.mkdir()
            memory_dir = root / "memory"; memory_dir.mkdir()
            (root / "configs").mkdir()
            (root / "configs" / "barinella.toml").write_text(f'memory_dir = "{memory_dir.as_posix()}"\nmemory_namespace = "barinella"\n', encoding="utf-8")
            target = root / "available-skills" / "memory-store" / "scripts"; target.mkdir(parents=True)
            shutil.copy2(SCRIPT.parents[4] / "available-skills" / "memory-store" / "scripts" / "memory_store.py", target / "memory_store.py")
            _, result = MODULE.dispatch(root, workspace, {"version": 1, "operation": "schema.apply", "manifest": MODULE.MANIFEST, "confirm": True})
            self.assertIn("applied", result)
            _, result = MODULE.dispatch(root, workspace, {"version": 1, "operation": "record.upsert", "table": "category_guidance", "key": ["category_id"], "data": {"category_id": 58, "aliases": "AWS"}, "confirm": True})
            self.assertIn("id", result)


if __name__ == "__main__": unittest.main()
