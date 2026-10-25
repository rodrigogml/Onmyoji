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
        return MODULE.Skill(identifier, identifier.title(), script.parent, "", script)

    def test_prompt_uses_the_shared_visual_prefix(self) -> None:
        with patch("builtins.input", return_value="x") as input_mock:
            self.assertEqual(MODULE.prompt("Opção: "), "x")
        input_mock.assert_called_once_with("› Opção: ")

    def test_initial_configuration_starts_uninstalled_without_instance_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data, message = MODULE.ensure_shikigami_configuration(root)
            self.assertIsNotNone(data, message)
            self.assertEqual(data["shikigami_identity"], "")
            self.assertFalse(data["shikigami_installed"])
            rendered = MODULE.system_path(root).read_text(encoding="utf-8")
            self.assertIn("[shikigami]", rendered)
            self.assertIn("installed = false", rendered)
            self.assertFalse((root / "shikigami" / "instance.toml").exists())

    def test_default_system_requires_a_discovered_or_configured_codex_executable(self) -> None:
        data = MODULE.default_system()
        self.assertEqual(data["executable"], "")
        self.assertEqual(data["model"], "gpt-6.1-sol")
        self.assertFalse(MODULE.validate_system(data)[0])

    def test_discovers_codex_executables_visible_in_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary_directory = Path(temporary)
            executable = binary_directory / "codex.cmd"; executable.write_text("", encoding="utf-8")
            with patch.dict(MODULE.os.environ, {"PATH": str(binary_directory)}, clear=False), patch.object(MODULE.shutil, "which", return_value=None):
                self.assertEqual(MODULE.discover_codex_executables(), [str(executable.resolve())])

    def test_codex_model_list_uses_the_current_catalog(self) -> None:
        self.assertEqual(MODULE.CODEX_MODELS, ["gpt-6.1-sol", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"])

    def test_reasoning_effort_is_selected_from_the_model_compatible_options(self) -> None:
        self.assertEqual(MODULE.reasoning_efforts_for_model("gpt-6-luna"), ["low", "medium", "high", "xhigh", "max"])
        self.assertEqual(MODULE.reasoning_efforts_for_model("gpt-6.1-sol"), ["low", "medium", "high", "xhigh", "max", "ultra"])
        self.assertEqual(MODULE.preferred_reasoning_effort("gpt-6-luna"), "high")

    def test_wizard_normalizes_name_and_creates_shikigami_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch("builtins.input", return_value="  Exemplo   da   Silva "), patch.object(MODULE, "configure_initial_codex", side_effect=lambda _root, data: data | {"executable": sys.executable}), patch.object(MODULE, "choose_project_directory", side_effect=lambda root, _current: str(root)), patch.object(MODULE, "choose_sandbox", return_value="workspace-write"), patch.object(MODULE, "choose_approval_policy", return_value="on-request"):
            root = Path(temporary)
            data, _message = MODULE.ensure_shikigami_configuration(root)
            installed = MODULE.initial_installation_wizard(root, data)
            self.assertIsNotNone(installed)
            self.assertEqual(installed["shikigami_identity"], "Exemplo da Silva")
            self.assertTrue(installed["shikigami_installed"])
            MODULE.ensure_shikigami_resources(root)
            self.assertTrue((root / "shikigami" / "README.md").is_file())
            self.assertFalse((root / "shikigami" / "instance.toml").exists())

    def test_workspace_suggestion_is_a_sibling_with_work_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "MeuShikigami"; root.mkdir()
            self.assertEqual(MODULE.suggested_project_directory(root), root.parent / "MeuShikigami-Work")

    def test_workspace_selection_creates_the_suggested_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch("builtins.input", side_effect=["", "s"]):
            root = Path(temporary) / "MeuShikigami"; root.mkdir()
            selected = MODULE.choose_project_directory(root, "")
            expected = root.parent / "MeuShikigami-Work"
            self.assertEqual(selected, str(expected.resolve()))
            self.assertTrue(expected.is_dir())

    def test_workspace_selection_does_not_change_configuration_when_creation_is_declined(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch("builtins.input", side_effect=["", "n"]):
            root = Path(temporary) / "MeuShikigami"; root.mkdir()
            self.assertIsNone(MODULE.choose_project_directory(root, ""))
            self.assertFalse((root.parent / "MeuShikigami-Work").exists())

    def test_sandbox_selection_explains_the_instance_and_workspace_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch("builtins.input", return_value="x"), patch.object(MODULE.sys, "stdout", io.StringIO()) as output:
            root = Path(temporary) / "MeuShikigami"; workspace = Path(temporary) / "MeuShikigami-Work"; root.mkdir(); workspace.mkdir()
            self.assertIsNone(MODULE.choose_sandbox(root, workspace, "workspace-write"))
            text = output.getvalue()
            self.assertIn(str(root.resolve()), text)
            self.assertIn(str(workspace.resolve()), text)
            self.assertIn("Não libera", text)

    def test_approval_policy_selection_explains_the_local_decision_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch("builtins.input", return_value="x"), patch.object(MODULE.sys, "stdout", io.StringIO()) as output:
            root = Path(temporary) / "MeuShikigami"; workspace = Path(temporary) / "MeuShikigami-Work"; root.mkdir(); workspace.mkdir()
            self.assertIsNone(MODULE.choose_approval_policy(root, workspace, "workspace-write", "on-request"))
            text = output.getvalue()
            self.assertIn(str(root.resolve()), text)
            self.assertIn(str(workspace.resolve()), text)
            self.assertIn("Nunca mostra pedidos", text)
            self.assertNotIn("untrusted", text)

    def test_codex_executable_validation_calls_version(self) -> None:
        completed = subprocess.CompletedProcess(["codex", "--version"], 0, "codex 1.0", "")
        with patch.object(MODULE.subprocess, "run", return_value=completed) as run:
            self.assertEqual(MODULE.validate_codex_executable("codex"), (True, "codex 1.0"))
        self.assertEqual(run.call_args.args[0], ["codex", "--version"])

    def test_codex_model_validation_runs_an_ephemeral_read_only_call(self) -> None:
        version = subprocess.CompletedProcess(["codex", "--version"], 0, "codex 1.0", "")
        model = subprocess.CompletedProcess(["codex", "exec"], 0, "OK", "")
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE.subprocess, "run", side_effect=[version, model]) as run:
            data = MODULE.default_system() | {"executable": "codex"}
            self.assertTrue(MODULE.validate_codex_model(Path(temporary), data)[0])
        self.assertEqual(run.call_args_list[1].args[0], ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only", "--model", "gpt-6.1-sol", "--config", 'model_reasoning_effort="medium"', "Responda exatamente: OK"])

    def test_model_validation_rejects_an_incompatible_reasoning_effort_before_execution(self) -> None:
        data = MODULE.default_system() | {"executable": "codex", "model": "gpt-6-luna", "model_reasoning_effort": "ultra"}
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "validate_codex_executable", return_value=(True, "codex 1.0")), patch.object(MODULE.subprocess, "run") as run:
            self.assertFalse(MODULE.validate_codex_model(Path(temporary), data)[0])
        run.assert_not_called()

    def test_diagnostics_check_all_reviewed_initial_setup_items(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "validate_codex_executable", return_value=(True, "codex 1.0")), patch.object(MODULE, "validate_codex_model", return_value=(True, "modelo respondeu")):
            data = MODULE.default_system() | {"shikigami_identity": "Exemplo", "shikigami_installed": True, "executable": "codex", "project_directory": temporary}
            MODULE.system_path(Path(temporary)).parent.mkdir()
            MODULE.system_path(Path(temporary)).write_text(MODULE.render_system(data), encoding="utf-8")
            diagnostics = MODULE.shikigami_diagnostics(Path(temporary))
        self.assertEqual([(item["state"], item["label"]) for item in diagnostics], [("ok", "Nome do Shikigami"), ("ok", "Executável do Codex-CLI"), ("ok", "Modelo do Codex-CLI"), ("ok", "Pasta de trabalho"), ("ok", "Sandbox"), ("ok", "Política de aprovação"), ("ok", "Diretórios adicionais de escrita")])

    def test_diagnostics_validate_each_additional_writable_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "MeuShikigami"; workspace = Path(temporary) / "MeuShikigami-Work"
            root.mkdir(); workspace.mkdir()
            data = MODULE.default_system() | {"executable": sys.executable, "project_directory": str(workspace), "additional_writable_directories": [str(root.parent)]}
            diagnostics = MODULE.writable_directories_diagnostics(root, data)
            self.assertEqual(diagnostics[0]["state"], "error")
            self.assertIn("diretórios anteriores", diagnostics[0]["detail"])

    def test_diagnostics_mark_extra_writable_directory_pending_when_sandbox_is_not_workspace_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "MeuShikigami"; workspace = Path(temporary) / "MeuShikigami-Work"
            root.mkdir(); workspace.mkdir(); (workspace / "exports").mkdir()
            data = MODULE.default_system() | {"project_directory": str(workspace), "sandbox_mode": "read-only", "additional_writable_directories": ["exports"]}
            diagnostics = MODULE.writable_directories_diagnostics(root, data)
            self.assertEqual(diagnostics[0]["state"], "pending")
            self.assertIn(str((workspace / "exports").resolve()), diagnostics[0]["detail"])

    def test_legacy_untrusted_approval_policy_is_migrated_to_on_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = MODULE.default_system() | {"executable": sys.executable, "project_directory": temporary, "approval_policy": "untrusted"}
            MODULE.system_path(root).parent.mkdir()
            MODULE.system_path(root).write_text(MODULE.render_system(data), encoding="utf-8")
            configured, message = MODULE.ensure_shikigami_configuration(root)
            self.assertIsNotNone(configured, message)
            self.assertEqual(configured["approval_policy"], "on-request")
            self.assertIn('approval_policy = "on-request"', MODULE.system_path(root).read_text(encoding="utf-8"))

    def test_legacy_instance_identity_is_migrated_before_file_removal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); legacy = root / "shikigami" / "instance.toml"; legacy.parent.mkdir()
            legacy.write_text('[shikigami]\nidentity = "  Legado   Nome "\nworkspace_directory_name = "unused"\n', encoding="utf-8")
            data, message = MODULE.ensure_shikigami_configuration(root)
            self.assertIsNotNone(data, message)
            self.assertEqual(data["shikigami_identity"], "Legado Nome")
            self.assertTrue(data["shikigami_installed"])
            self.assertFalse(legacy.exists())

    def test_noninteractive_actions_report_pending_initial_installation_without_prompting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "ROOT", Path(temporary)), patch.object(sys, "argv", [str(SCRIPT), "--action", "status", "--json"]), patch.object(MODULE.sys, "stdout", io.StringIO()) as output:
            self.assertEqual(MODULE.main(), 2)
            response = json.loads(output.getvalue())
            self.assertEqual(response["error"]["code"], "initial_installation_pending")

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

    def test_writable_directory_menu_keeps_relative_path_and_resolves_it_from_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch("builtins.input", side_effect=["1", "exports", "PERMITIR", "x"]):
            root = Path(temporary) / "MeuShikigami"; workspace = Path(temporary) / "MeuShikigami-Work"
            root.mkdir(); workspace.mkdir(); (workspace / "exports").mkdir()
            data = MODULE.default_system() | {"executable": sys.executable, "project_directory": str(workspace)}
            configured = MODULE.manage_writable_directories(root, data)
            self.assertEqual(configured["additional_writable_directories"], ["exports"])
            self.assertEqual(MODULE.resolve_writable_directory(configured, "exports"), (workspace / "exports").resolve())

    def test_writable_directories_reject_duplicates_after_relative_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "MeuShikigami"; workspace = Path(temporary) / "MeuShikigami-Work"
            root.mkdir(); workspace.mkdir(); (workspace / "exports").mkdir()
            data = MODULE.default_system() | {"executable": sys.executable, "project_directory": str(workspace), "additional_writable_directories": ["exports", ".\\exports"]}
            valid, message = MODULE.validate_system(data, root=root)
            self.assertFalse(valid)
            self.assertIn("já está configurado", message)

    def test_writable_directories_reject_instance_workspace_and_their_parents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "MeuShikigami"; workspace = Path(temporary) / "MeuShikigami-Work"
            root.mkdir(); workspace.mkdir()
            data = MODULE.default_system() | {"executable": sys.executable, "project_directory": str(workspace)}
            for prohibited in (str(root), str(workspace), str(root.parent)):
                valid, message = MODULE.validate_system(data | {"additional_writable_directories": [prohibited]}, root=root)
                self.assertFalse(valid)
                self.assertIn("diretórios anteriores", message)

    def test_writable_directory_parent_of_existing_root_requires_expansion_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "MeuShikigami"; workspace = Path(temporary) / "MeuShikigami-Work"
            root.mkdir(); workspace.mkdir(); (workspace / "shared" / "reports").mkdir(parents=True)
            data = MODULE.default_system() | {"executable": sys.executable, "project_directory": str(workspace), "additional_writable_directories": ["shared/reports"]}
            allowed, message = MODULE.writable_directory_relation(root, data, (workspace / "shared").resolve(), ["shared/reports"])
            self.assertTrue(allowed)
            self.assertIn("amplia", message)

    def test_writable_directory_menu_removes_a_selected_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch("builtins.input", side_effect=["2", "1", "x"]):
            root = Path(temporary) / "MeuShikigami"; workspace = Path(temporary) / "MeuShikigami-Work"
            root.mkdir(); workspace.mkdir(); (workspace / "exports").mkdir()
            data = MODULE.default_system() | {"executable": sys.executable, "project_directory": str(workspace), "additional_writable_directories": ["exports"]}
            configured = MODULE.manage_writable_directories(root, data)
            self.assertEqual(configured["additional_writable_directories"], [])

    def test_writable_directory_menu_removes_all_entries_only_after_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch("builtins.input", side_effect=["3", "REMOVER", "x"]):
            root = Path(temporary) / "MeuShikigami"; workspace = Path(temporary) / "MeuShikigami-Work"
            root.mkdir(); workspace.mkdir(); (workspace / "exports").mkdir(); (workspace / "reports").mkdir()
            data = MODULE.default_system() | {"executable": sys.executable, "project_directory": str(workspace), "additional_writable_directories": ["exports", "reports"]}
            configured = MODULE.manage_writable_directories(root, data)
            self.assertEqual(configured["additional_writable_directories"], [])

    def test_writable_directory_menu_does_not_save_a_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch("builtins.input", side_effect=["1", "missing", "x"]):
            root = Path(temporary) / "MeuShikigami"; workspace = Path(temporary) / "MeuShikigami-Work"
            root.mkdir(); workspace.mkdir()
            data = MODULE.default_system() | {"executable": sys.executable, "project_directory": str(workspace)}
            configured = MODULE.manage_writable_directories(root, data)
            self.assertEqual(configured["additional_writable_directories"], [])

    def test_writable_directory_broad_scope_detects_user_profile_and_volume_root(self) -> None:
        self.assertTrue(MODULE.is_broad_writable_directory(Path.home().resolve()))
        self.assertTrue(MODULE.is_broad_writable_directory(Path(Path.home().anchor)))

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

    def test_launches_device_login_without_a_local_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = MODULE.default_system() | {"executable": sys.executable}
            with patch.object(MODULE.subprocess, "run") as run:
                MODULE.launch_codex(root, data, login=True, device_auth=True)
            self.assertEqual(run.call_args.args[0], [sys.executable, "login", "--device-auth"])

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
            self.assertEqual(active.resolve(), skill.source.resolve())
            self.assertEqual(MODULE.enabled_ids(root), {"omie"})
            self.assertTrue(MODULE.save_enabled(root, set(), [skill])[0])
            self.assertFalse(active.exists() or active.is_symlink())

    def test_desktop_skills_link_uses_the_shikigami_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "project"; project.mkdir()
            data = MODULE.default_system() | {"project_directory": str(project)}
            self.assertTrue(MODULE.set_desktop_skills_enabled(root, data, True)[0])
            link = root / ".agents" / "skills"
            self.assertTrue(MODULE.desktop_skills_enabled(root, data))
            self.assertEqual(link.resolve(), MODULE.active_skills_path(root).resolve())
            self.assertFalse((project / ".agents").exists())
            self.assertTrue(MODULE.set_desktop_skills_enabled(root, data, False)[0])
            self.assertFalse(link.exists() or link.is_symlink())
            self.assertFalse((root / ".agents").exists())

    def test_desktop_skills_disable_preserves_other_agents_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); data = MODULE.default_system()
            self.assertTrue(MODULE.set_desktop_skills_enabled(root, data, True)[0])
            marker = root / ".agents" / "other.txt"; marker.write_text("keep", encoding="utf-8")
            ok, message = MODULE.set_desktop_skills_enabled(root, data, False)
            self.assertTrue(ok)
            self.assertIn("preservada", message)
            self.assertTrue(marker.is_file())

    def test_desktop_skills_refuses_an_unmanaged_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); data = MODULE.default_system()
            destination = root / ".agents" / "skills"; destination.mkdir(parents=True)
            self.assertFalse(MODULE.set_desktop_skills_enabled(root, data, True)[0])
            self.assertFalse(MODULE.set_desktop_skills_enabled(root, data, False)[0])
            self.assertTrue(destination.is_dir())

    def test_desktop_skills_foreign_junction_is_not_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); data = MODULE.default_system(); foreign = root / "foreign"; foreign.mkdir()
            destination = root / ".agents" / "skills"; destination.parent.mkdir()
            MODULE.create_skill_link(foreign, destination)
            self.assertFalse(MODULE.desktop_skills_enabled(root, data))
            ok, message = MODULE.set_desktop_skills_enabled(root, data, True)
            self.assertFalse(ok)
            self.assertIn("outro destino", message)

    def test_instance_skill_link_does_not_change_catalog_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "shikigami" / "skills" / "barinella"; source.mkdir(parents=True)
            skill = MODULE.Skill("barinella", "Barinella", source, "", catalog_managed=False)
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

    def test_discovers_instance_skill_markdown_without_setup_script(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "shikigami" / "skills" / "domain-knowledge"; source.mkdir(parents=True)
            (source / "SKILL.md").write_text("# Domain knowledge\n", encoding="utf-8")
            skills = MODULE.discover(root)
            self.assertEqual(len(skills), 1)
            skill = skills[0]
            self.assertEqual(skill.identifier, "domain-knowledge")
            self.assertEqual(skill.source, source)
            self.assertIsNone(skill.setup_script)
            self.assertFalse(skill.catalog_managed)
            self.assertTrue(MODULE.set_instance_skill_enabled(root, skill, True)[0])
            self.assertEqual((MODULE.active_skills_path(root) / skill.identifier).resolve(), source.resolve())
            self.assertTrue(MODULE.set_instance_skill_enabled(root, skill, False)[0])

    def test_catalog_sync_preserves_homonymous_instance_link_when_catalog_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog_source = root / "available-skills" / "omie"; catalog_source.mkdir(parents=True)
            instance_source = root / "shikigami" / "skills" / "omie"; instance_source.mkdir(parents=True)
            instance_skill = MODULE.Skill("omie", "Omie", instance_source, "", catalog_managed=False)
            catalog_skill = MODULE.Skill("omie", "Omie", catalog_source, "", catalog_source / "setupSkill.py")
            self.assertTrue(MODULE.set_instance_skill_enabled(root, instance_skill, True)[0])
            self.assertTrue(MODULE.sync_active_skill_links(root, set(), [catalog_skill])[0])
            self.assertEqual((MODULE.active_skills_path(root) / "omie").resolve(), instance_source.resolve())

    def test_conflicting_catalog_and_instance_ids_cannot_be_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog_source = root / "available-skills" / "omie"; catalog_source.mkdir(parents=True)
            instance_source = root / "shikigami" / "skills" / "omie"; instance_source.mkdir(parents=True)
            catalog_skill = MODULE.Skill("omie", "Omie", catalog_source, "", catalog_source / "setupSkill.py")
            instance_skill = MODULE.Skill("omie", "Omie", instance_source, "", catalog_managed=False)
            ok, message = MODULE.save_enabled(root, {"omie"}, [catalog_skill, instance_skill])
            self.assertFalse(ok)
            self.assertIn("conflito", message)

    def test_menu_places_domain_skills_in_the_right_column_when_wide(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            integration = MODULE.Skill("omie", "Omie", root / "available-skills" / "omie", "", root / "available-skills" / "omie" / "setupSkill.py")
            domain = MODULE.Skill("barinella", "Barinella", root / "shikigami" / "skills" / "barinella", "", catalog_managed=False)
            output = io.StringIO()
            with patch("builtins.input", return_value="x"), patch.object(MODULE.shutil, "get_terminal_size", return_value=os.terminal_size((120, 24))), patch.object(MODULE.sys, "stdout", output):
                self.assertEqual(MODULE.menu([integration, domain], root), 0)
            first_header = next(line for line in output.getvalue().splitlines() if "SKILLS DE INTEGRAÇÃO" in line)
            self.assertIn("SKILLS DE DOMÍNIO", first_header)
            self.assertLess(first_header.index("SKILLS DE INTEGRAÇÃO"), first_header.index("SKILLS DE DOMÍNIO"))
            self.assertEqual(first_header.index("SKILLS DE DOMÍNIO"), 52)

    def test_skill_status_badges_keep_the_menu_column_width(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE.Ui, "enabled", False):
            root = Path(temporary)
            skill = MODULE.Skill("omie", "Omie", root / "available-skills" / "omie", "")
            active = MODULE.skill_item(1, skill, MODULE.Ui.badge("ATIVA", "active"))
            inactive = MODULE.skill_item(1, skill, MODULE.Ui.badge("inativa", "inactive"))
            conflict = MODULE.skill_item(1, skill, MODULE.Ui.badge("CONFLITO", "warning"))
            self.assertEqual(len(active), 50)
            self.assertEqual(len(inactive), 50)
            self.assertEqual(len(conflict), 50)

    def test_stale_python_cache_is_replaced_by_an_active_skill_link(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); skill = self.fake_skill(root)
            stale = MODULE.active_skills_path(root) / "omie" / "scripts" / "__pycache__"
            stale.mkdir(parents=True)
            (stale / "omie.cpython-312.pyc").write_bytes(b"cache")
            self.assertTrue(MODULE.save_enabled(root, {"omie"}, [skill])[0])
            self.assertEqual((MODULE.active_skills_path(root) / "omie").resolve(), skill.source.resolve())

    def test_legacy_skill_config_is_migrated_to_local_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); skill = self.fake_skill(root)
            MODULE.config_path(root).write_text(f'{MODULE.MANAGED_BEGIN}\n[[skills.config]]\npath = "{skill.source.as_posix()}"\nenabled = true\n{MODULE.MANAGED_END}\n', encoding="utf-8")
            self.assertTrue(MODULE.ensure_skill_state(root, [skill])[0])
            self.assertEqual(MODULE.enabled_ids(root), {"omie"})
            self.assertNotIn(MODULE.MANAGED_BEGIN, MODULE.config_path(root).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
