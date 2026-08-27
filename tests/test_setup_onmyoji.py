from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "setupOnmyoji.py"
SPEC = importlib.util.spec_from_file_location("onmyoji_setup", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class SystemSetupTests(unittest.TestCase):
    def fake_skill(self, root: Path, identifier: str = "omie") -> object:
        script = root / "available-skills" / identifier / "setupSkill.py"
        script.parent.mkdir(parents=True)
        script.write_text("", encoding="utf-8")
        return MODULE.Skill(identifier, identifier.title(), script, "")

    def test_prompt_uses_the_shared_visual_prefix(self) -> None:
        with patch("builtins.input", return_value="x") as input_mock:
            self.assertEqual(MODULE.prompt("Opção: "), "x")
        input_mock.assert_called_once_with("› Opção: ")

    def test_system_settings_are_local_and_applied_to_codex_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = MODULE.default_system() | {"executable": sys.executable, "project_directory": str(root), "model": "gpt-5.6-terra", "model_reasoning_effort": "high"}
            ok, _message = MODULE.save_system(root, data)
            self.assertTrue(ok)
            self.assertTrue(MODULE.system_path(root).is_file())
            codex = MODULE.config_path(root).read_text(encoding="utf-8")
            self.assertIn('model = "gpt-5.6-terra"', codex)
            self.assertIn('model_reasoning_effort = "high"', codex)
            self.assertIn('[sandbox_workspace_write]', codex)
            self.assertNotIn("project_directory", codex)

    def test_invalid_project_path_does_not_replace_saved_system_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = MODULE.default_system() | {"executable": sys.executable, "project_directory": str(root)}
            self.assertTrue(MODULE.save_system(root, data)[0])
            bad = data | {"project_directory": str(root / "missing")}
            self.assertFalse(MODULE.save_system(root, bad)[0])
            self.assertEqual(MODULE.load_system(root)["project_directory"], str(root))

    def test_launch_passes_model_reasoning_and_writable_directories_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"; project.mkdir()
            extra = root / "extra"; extra.mkdir()
            data = MODULE.default_system() | {"executable": sys.executable, "project_directory": str(project), "model": "gpt-5.6-sol", "model_reasoning_effort": "xhigh", "additional_writable_directories": [str(extra)]}
            with patch.object(MODULE.subprocess, "run") as run:
                MODULE.launch_codex(root, data)
            command = run.call_args.args[0]
            self.assertEqual(command[:6], [sys.executable, "-C", str(project), "-m", "gpt-5.6-sol", "-c"])
            self.assertIn('model_reasoning_effort = "xhigh"', command)
            self.assertEqual(command[-2:], ["--add-dir", str(extra)])

    def test_enabled_skills_are_linked_from_the_local_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); skill = self.fake_skill(root)
            self.assertTrue(MODULE.save_enabled(root, {"omie"}, [skill])[0])
            active = MODULE.active_skills_path(root) / "omie"
            self.assertTrue(active.exists())
            self.assertEqual(active.resolve(), skill.script.parent.resolve())
            self.assertEqual(MODULE.enabled_ids(root), {"omie"})
            self.assertTrue(MODULE.save_enabled(root, set(), [skill])[0])
            self.assertFalse(active.exists() or active.is_symlink())

    def test_stale_python_cache_is_replaced_by_an_active_skill_link(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); skill = self.fake_skill(root)
            stale = MODULE.active_skills_path(root) / "omie" / "scripts" / "__pycache__"
            stale.mkdir(parents=True)
            (stale / "omie.cpython-312.pyc").write_bytes(b"cache")
            self.assertTrue(MODULE.save_enabled(root, {"omie"}, [skill])[0])
            self.assertEqual((MODULE.active_skills_path(root) / "omie").resolve(), skill.script.parent.resolve())

    def test_legacy_skill_config_is_migrated_to_local_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); skill = self.fake_skill(root)
            MODULE.config_path(root).write_text(f'{MODULE.MANAGED_BEGIN}\n[[skills.config]]\npath = "{skill.script.parent.as_posix()}"\nenabled = true\n{MODULE.MANAGED_END}\n', encoding="utf-8")
            self.assertTrue(MODULE.ensure_skill_state(root, [skill])[0])
            self.assertEqual(MODULE.enabled_ids(root), {"omie"})
            self.assertNotIn(MODULE.MANAGED_BEGIN, MODULE.config_path(root).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
