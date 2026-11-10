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
        self.assertEqual(MODULE.identifier_for("  Tsuki Ágil  "), "tsuki-agil")
        with self.assertRaises(MODULE.BootstrapError):
            MODULE.identifier_for("CON")

    def test_directory_component_preserves_case_and_normalizes_only_spaces(self) -> None:
        self.assertEqual(MODULE.directory_component_for("  Lavelinha  AI  "), "Lavelinha-AI")

    def test_directory_name_is_limited_to_a_child_of_the_base_directory(self) -> None:
        self.assertEqual(MODULE.directory_name_for("Shikigami-Tsuki", "Nome da pasta"), "Shikigami-Tsuki")
        for value in ("C:\\opt\\Tsuki", "../Tsuki", "Tsuki/Work"):
            with self.subTest(value=value), self.assertRaises(MODULE.BootstrapError):
                MODULE.directory_name_for(value, "Nome da pasta")

    def test_copy_template_projects_files_and_replaces_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); template = root / "template"; destination = root / "instance"
            template.mkdir(); (template / "README.md").write_text("# {{SHIKIGAMI_NAME}}", encoding="utf-8")
            (template / ".gitignore").write_text("cache", encoding="utf-8")
            MODULE.copy_template(template, destination, {"{{SHIKIGAMI_NAME}}": "Tsuki"})
            self.assertEqual((destination / "README.md").read_text(encoding="utf-8"), "# Tsuki")
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

    def test_merge_protection_guards_setup_scripts_and_prioritizes_upstream(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "git") as command:
            repository = Path(temporary); (repository / ".git" / "info").mkdir(parents=True)

            MODULE.configure_merge_protection(repository)

            attributes = (repository / ".git" / "info" / "attributes").read_text(encoding="utf-8")
            hook = (repository / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")
        self.assertIn("onmyoji-daemon/setupMcp.py merge=shikigami-upstream", attributes)
        self.assertIn("setupOnmyoji.py merge=shikigami-upstream", attributes)
        self.assertIn("instructions.md merge=shikigami-local", attributes)
        self.assertIn("environment.toml merge=shikigami-local", attributes)
        self.assertIn("onmyoji-setup-guard", hook)
        self.assertIn("onmyoji-daemon/setupDaemon.py", hook)
        self.assertTrue(any(call.args[0][:2] == ["config", "merge.shikigami-upstream.driver"] for call in command.call_args_list))

    def test_setup_guard_rejects_an_instance_commit_that_changes_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            for path in MODULE.UPSTREAM_SETUP_FILES:
                target = repository / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_text("base\n", encoding="utf-8")
            for arguments in (["init"], ["config", "user.name", "Teste"], ["config", "user.email", "teste@example.test"], ["add", "."], ["commit", "-m", "Base"]):
                subprocess.run(["git", *arguments], cwd=repository, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
            MODULE.configure_merge_protection(repository)
            target = repository / "onmyoji-daemon" / "setupMcp.py"; target.write_text("alterado\n", encoding="utf-8")
            subprocess.run(["git", "add", str(target.relative_to(repository))], cwd=repository, check=True, capture_output=True, text=True)

            completed = subprocess.run(["git", "commit", "-m", "Alteração indevida"], cwd=repository, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("pertence ao Onmyōji", completed.stderr)

    def test_merge_rules_keep_template_identity_and_take_upstream_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            for arguments in (["init", "-b", "main"], ["config", "user.name", "Teste"], ["config", "user.email", "teste@example.test"]):
                subprocess.run(["git", *arguments], cwd=repository, check=True, capture_output=True)
            (repository / "instructions.md").write_text("base\n", encoding="utf-8")
            (repository / "setupOnmyoji.py").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repository, check=True); subprocess.run(["git", "commit", "-m", "Base"], cwd=repository, check=True, capture_output=True)
            subprocess.run(["git", "branch", "upstream-change"], cwd=repository, check=True)
            (repository / "instructions.md").write_text("instância\n", encoding="utf-8")
            (repository / "setupOnmyoji.py").write_text("instância\n", encoding="utf-8")
            subprocess.run(["git", "commit", "-am", "Personaliza instância"], cwd=repository, check=True, capture_output=True)
            subprocess.run(["git", "switch", "upstream-change"], cwd=repository, check=True, capture_output=True)
            (repository / "instructions.md").write_text("template novo\n", encoding="utf-8")
            (repository / "setupOnmyoji.py").write_text("setup novo\n", encoding="utf-8")
            subprocess.run(["git", "commit", "-am", "Atualiza Onmyoji"], cwd=repository, check=True, capture_output=True)
            subprocess.run(["git", "switch", "main"], cwd=repository, check=True, capture_output=True)

            MODULE.configure_merge_protection(repository)
            merged = subprocess.run(["git", "merge", "--no-edit", "upstream-change"], cwd=repository, text=True, capture_output=True, check=False)

            self.assertEqual(merged.returncode, 0, merged.stderr)
            self.assertEqual((repository / "instructions.md").read_text(encoding="utf-8"), "instância\n")
            self.assertEqual((repository / "setupOnmyoji.py").read_text(encoding="utf-8"), "setup novo\n")

    def test_interactive_cancellation_does_not_create_instance(self) -> None:
        answers = iter(["Tsuki", "", "", "", "", "", "n"])
        output: list[str] = []
        with patch.object(MODULE, "create_instance") as create:
            result = MODULE.interactive(lambda _prompt: next(answers), output.append)
        self.assertEqual(result, 0)
        create.assert_not_called()
        self.assertIn("Instalação cancelada.", output[-1])

    def test_interactive_starts_the_setup_after_creating_the_instance(self) -> None:
        answers = iter(["Tsuki", "", "", "", "", "", "s"])
        with patch.object(MODULE, "create_instance") as create, patch.object(MODULE, "clear_screen") as clear, patch.object(MODULE, "run_instance_setup", return_value=0) as setup:
            result = MODULE.interactive(lambda _prompt: next(answers), lambda _message: None)
        self.assertEqual(result, 0)
        create.assert_called_once()
        clear.assert_called_once()
        setup.assert_called_once_with(Path.cwd() / "Shikigami-Tsuki")

    def test_run_instance_setup_uses_the_bootstrap_python_and_instance_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            instance = Path(temporary); (instance / "setupOnmyoji.py").touch()
            completed = subprocess.CompletedProcess([], 0)
            with patch.object(MODULE.subprocess, "run", return_value=completed) as run:
                self.assertEqual(MODULE.run_instance_setup(instance), 0)
        run.assert_called_once_with([sys.executable, str(instance / "setupOnmyoji.py")], cwd=str(instance), check=False)

    def test_setup_import_does_not_require_aiohttp_before_runtime_installation(self) -> None:
        program = "import runpy,sys;sys.modules['aiohttp']=None;runpy.run_path(sys.argv[1],run_name='setup_import_test')"
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", program, str(SCRIPT.parent / "setupOnmyoji.py")],
            cwd=str(SCRIPT.parent),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
