from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "bootstrapShikigami.py"
SPEC = importlib.util.spec_from_file_location("bootstrap_shikigami", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class BootstrapShikigamiTests(unittest.TestCase):
    def test_opening_logo_uses_the_setup_header_width(self) -> None:
        output = io.StringIO()
        with patch.object(MODULE, "terminal_width", return_value=56), patch.object(MODULE.sys, "stdout", output):
            MODULE.opening_logo()
        self.assertEqual(output.getvalue().splitlines(), [line.center(56) for line in MODULE.TEXT_LOGO.splitlines()])

    def test_identifier_normalizes_name_and_rejects_reserved_windows_name(self) -> None:
        self.assertEqual(MODULE.identifier_for("  Akuma Ágil  "), "akuma-agil")
        with self.assertRaises(MODULE.BootstrapError):
            MODULE.identifier_for("CON")

    def test_directory_component_preserves_case_and_normalizes_only_spaces(self) -> None:
        self.assertEqual(MODULE.directory_component_for("  Lavelinha  AI  "), "Lavelinha-AI")

    def test_directory_name_is_limited_to_a_child_of_the_base_directory(self) -> None:
        self.assertEqual(MODULE.directory_name_for("Shikigami-Akuma", "Nome da pasta"), "Shikigami-Akuma")
        for value in ("C:\\opt\\Akuma", "../Akuma", "Akuma/Work"):
            with self.subTest(value=value), self.assertRaises(MODULE.BootstrapError):
                MODULE.directory_name_for(value, "Nome da pasta")

    def test_copy_template_projects_files_and_replaces_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); template = root / "template"; destination = root / "instance"
            template.mkdir(); (template / "README.md").write_text("# {{SHIKIGAMI_NAME}}", encoding="utf-8")
            (template / ".gitignore").write_text("cache", encoding="utf-8")
            MODULE.copy_template(template, destination, {"{{SHIKIGAMI_NAME}}": "Akuma"})
            self.assertEqual((destination / "README.md").read_text(encoding="utf-8"), "# Akuma")
            self.assertEqual((destination / ".gitignore").read_text(encoding="utf-8"), "cache")

    def test_initial_configuration_preserves_the_shikigami_name_for_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            MODULE.write_initial_configuration(repository, "Lavelinha")
            saved = tomllib.loads((repository / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8"))
        self.assertEqual(saved["shikigami"], {"identity": "Lavelinha", "installed": False})

    def test_remote_is_empty_uses_git_reference_listing(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch.object(MODULE, "git", return_value=completed) as command:
            self.assertTrue(MODULE.remote_is_empty("https://example.test/shikigami.git"))
        command.assert_called_once_with(["ls-remote", "--heads", "--tags", "https://example.test/shikigami.git"], capture=True)

    def test_require_new_directory_rejects_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            with self.assertRaises(MODULE.BootstrapError):
                MODULE.require_new_directory(path, "A pasta da instância")

    def test_configure_identity_prompts_only_for_missing_values(self) -> None:
        values = iter([subprocess.CompletedProcess([], 1, stdout="", stderr=""), subprocess.CompletedProcess([], 1, stdout="", stderr=""), subprocess.CompletedProcess([], 0, stdout="", stderr=""), subprocess.CompletedProcess([], 0, stdout="", stderr="")])
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "git", side_effect=lambda *args, **kwargs: next(values)) as command:
            MODULE.configure_identity(Path(temporary), lambda prompt: "Autor" if "Nome" in prompt else "autor@example.test")
        self.assertEqual(command.call_count, 4)

    def test_interactive_cancellation_does_not_create_instance(self) -> None:
        answers = iter(["Akuma", "", "", "", "", "", "n"])
        output: list[str] = []
        with patch.object(MODULE, "create_instance") as create:
            result = MODULE.interactive(lambda _prompt: next(answers), output.append)
        self.assertEqual(result, 0)
        create.assert_not_called()
        self.assertIn("Instalação cancelada.", output[-1])

    def test_interactive_starts_the_setup_after_creating_the_instance(self) -> None:
        answers = iter(["Akuma", "", "", "", "", "", "s"])
        with patch.object(MODULE, "create_instance") as create, patch.object(MODULE, "clear_screen") as clear, patch.object(MODULE, "run_instance_setup", return_value=0) as setup:
            result = MODULE.interactive(lambda _prompt: next(answers), lambda _message: None)
        self.assertEqual(result, 0)
        create.assert_called_once()
        clear.assert_called_once()
        setup.assert_called_once_with(Path.cwd() / "Shikigami-Akuma")

    def test_run_instance_setup_uses_the_bootstrap_python_and_instance_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            instance = Path(temporary); (instance / "setupOnmyoji.py").touch()
            completed = subprocess.CompletedProcess([], 0)
            with patch.object(MODULE.subprocess, "run", return_value=completed) as run:
                self.assertEqual(MODULE.run_instance_setup(instance), 0)
        run.assert_called_once_with([sys.executable, str(instance / "setupOnmyoji.py")], cwd=str(instance), check=False)
