from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
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

    def test_launches_native_codex_with_workspace_and_writable_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"; project.mkdir()
            extra = root / "extra"; extra.mkdir()
            data = MODULE.default_system() | {"executable": sys.executable, "project_directory": str(project), "model": "gpt-5.6-sol", "model_reasoning_effort": "xhigh", "additional_writable_directories": [str(extra)]}
            with patch.object(MODULE.subprocess, "run") as run:
                MODULE.launch_codex(root, data)
            command = run.call_args.args[0]
            self.assertEqual(command, [sys.executable, "--add-dir", str(extra)])
            self.assertEqual(run.call_args.kwargs["cwd"], str(project))
            environment = run.call_args.kwargs["env"]
            self.assertEqual(environment["CODEX_HOME"], str(root))

    def test_launches_onmyoji_console_separately_from_native_codex(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "project"; project.mkdir()
            data = MODULE.default_system() | {"executable": sys.executable, "project_directory": str(project)}
            with patch.object(MODULE.subprocess, "run") as run:
                MODULE.launch_onmyoji_interactive(root, data)
            command = run.call_args.args[0]
            self.assertEqual(command[:5], [sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root)])
            self.assertEqual(command[5], "interactive")

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

    def test_desktop_skills_link_uses_the_configured_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "project"; project.mkdir()
            data = MODULE.default_system() | {"project_directory": str(project)}
            self.assertTrue(MODULE.set_desktop_skills_enabled(root, data, True)[0])
            link = project / ".agents" / "skills"
            self.assertTrue(MODULE.desktop_skills_enabled(root, data))
            self.assertEqual(link.resolve(), MODULE.active_skills_path(root).resolve())
            self.assertTrue(MODULE.set_desktop_skills_enabled(root, data, False)[0])
            self.assertFalse(link.exists() or link.is_symlink())
            self.assertFalse((project / ".agents").exists())

    def test_desktop_skills_disable_preserves_other_agents_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "project"; project.mkdir()
            data = MODULE.default_system() | {"project_directory": str(project)}
            self.assertTrue(MODULE.set_desktop_skills_enabled(root, data, True)[0])
            marker = project / ".agents" / "other.txt"; marker.write_text("keep", encoding="utf-8")
            ok, message = MODULE.set_desktop_skills_enabled(root, data, False)
            self.assertTrue(ok)
            self.assertIn("preservada", message)
            self.assertTrue(marker.is_file())

    def test_desktop_skills_refuses_an_unmanaged_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "project"; project.mkdir()
            data = MODULE.default_system() | {"project_directory": str(project)}
            destination = project / ".agents" / "skills"; destination.mkdir(parents=True)
            self.assertFalse(MODULE.set_desktop_skills_enabled(root, data, True)[0])
            self.assertFalse(MODULE.set_desktop_skills_enabled(root, data, False)[0])
            self.assertTrue(destination.is_dir())

    def test_instance_skill_link_does_not_change_catalog_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "shikigami" / "skills" / "barinella"; source.mkdir(parents=True)
            skill = MODULE.Skill("barinella", "Barinella", source / "setupSkill.py", "", catalog_managed=False)
            self.assertTrue(MODULE.set_instance_skill_enabled(root, skill, True)[0])
            self.assertTrue(MODULE.is_skill_enabled(root, skill))
            self.assertFalse(MODULE.skill_state_path(root).exists())
            self.assertTrue(MODULE.set_instance_skill_enabled(root, skill, False)[0])
            self.assertFalse(MODULE.is_skill_enabled(root, skill))

    def test_discovers_instance_setup_scripts_separately_from_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for script in (root / "available-skills" / "omie" / "setupSkill.py", root / "shikigami" / "skills" / "barinella" / "setupSkill.py"):
                script.parent.mkdir(parents=True); script.write_text("", encoding="utf-8")
            def describe(command, **_kwargs):
                identifier = Path(command[1]).parent.name
                return subprocess.CompletedProcess(command, 0, json.dumps({"id": identifier, "title": identifier.title()}), "")
            with patch.object(MODULE.subprocess, "run", side_effect=describe):
                skills = {skill.identifier: skill for skill in MODULE.discover(root)}
            self.assertTrue(skills["omie"].catalog_managed)
            self.assertFalse(skills["barinella"].catalog_managed)

    def test_menu_places_domain_skills_in_the_right_column_when_wide(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            integration = MODULE.Skill("omie", "Omie", root / "available-skills" / "omie" / "setupSkill.py", "")
            domain = MODULE.Skill("barinella", "Barinella", root / "shikigami" / "skills" / "barinella" / "setupSkill.py", "", catalog_managed=False)
            output = io.StringIO()
            with patch("builtins.input", return_value="x"), patch.object(MODULE.shutil, "get_terminal_size", return_value=os.terminal_size((120, 24))), patch.object(MODULE.sys, "stdout", output):
                self.assertEqual(MODULE.menu([integration, domain], root), 0)
            first_header = next(line for line in output.getvalue().splitlines() if "SKILLS DE INTEGRAÇÃO" in line)
            self.assertIn("SKILLS DE DOMÍNIO", first_header)
            self.assertLess(first_header.index("SKILLS DE INTEGRAÇÃO"), first_header.index("SKILLS DE DOMÍNIO"))

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
