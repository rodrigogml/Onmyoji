from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPT = Path(__file__).resolve().parents[1] / "setupOnmyoji.py"
SPEC = importlib.util.spec_from_file_location("onmyoji_setup", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class SystemSetupTests(unittest.TestCase):
    def fake_skill(self, root: Path, identifier: str = "omie") -> object:
        script = root / "shikigami-skills" / identifier / "setupSkill.py"
        script.parent.mkdir(parents=True)
        script.write_text("", encoding="utf-8")
        (script.parent / "SKILL.md").write_text(f"---\nname: {identifier}\ndescription: Teste\n---\n", encoding="utf-8")
        return MODULE.Skill(identifier, identifier.title(), script.parent, "", script)

    def test_prompt_uses_the_shared_visual_prefix(self) -> None:
        with patch("builtins.input", return_value="x") as input_mock:
            self.assertEqual(MODULE.prompt("Opção: "), "x")
        input_mock.assert_called_once_with("› Opção: ")

    def test_opening_logo_uses_the_same_width_as_menu_headers(self) -> None:
        output = io.StringIO()
        with patch.object(MODULE, "terminal_width", return_value=56), patch.object(MODULE.sys, "stdout", output):
            MODULE.opening_logo()
        self.assertEqual(output.getvalue().splitlines(), [line.center(56) for line in MODULE.TEXT_LOGO.splitlines()])

    def test_flow_choice_uses_numeric_default_without_opening_a_screen(self) -> None:
        with patch.object(MODULE, "prompt", return_value="") as prompt_mock, patch.object(MODULE, "screen") as screen_mock:
            selected = MODULE.flow_choice("Escolha a credencial", ["Criar", "Reutilizar"], "Reutilizar")
        self.assertEqual(selected, "Reutilizar")
        prompt_mock.assert_called_once_with("Opção [2]: ")
        screen_mock.assert_not_called()

    def test_navigable_choice_uses_the_numeric_default(self) -> None:
        with patch.object(MODULE, "prompt", return_value="") as prompt_mock, patch.object(MODULE, "screen"):
            selected = MODULE.choose("Menu de teste", ["Primeira", "Segunda"], "Segunda")
        self.assertEqual(selected, "Segunda")
        prompt_mock.assert_called_once_with("Opção [2]: ")

    def test_miniapps_credential_reference_reuses_existing_mcp_secret(self) -> None:
        configuration = MagicMock(); configuration.vault_profile_names.return_value = ["principal"]
        resolver = MagicMock(); resolver.entry_exists.return_value = True; resolver.password_exists.return_value = True
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "McpConfiguration", return_value=configuration), patch.object(MODULE, "KeePassCredentialResolver", return_value=resolver), patch.object(MODULE, "flow_choice", side_effect=["principal", "Usar a chave já salva"]), patch.object(MODULE, "prompt", return_value=""):
            selected = MODULE.configure_mcp_password_reference(Path(temporary), "Cloudflare Tunnel", "APIs/Cloudflare/MiniApps")
        self.assertEqual(selected, ("principal", "APIs/Cloudflare/MiniApps"))
        resolver.password_exists.assert_called_once_with("principal", "APIs/Cloudflare/MiniApps")
        resolver.save_password.assert_not_called()

    def test_diagnostics_are_a_flow_view_not_a_navigable_menu(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "flow_heading") as heading, patch.object(MODULE, "screen") as screen, patch.object(MODULE, "shikigami_diagnostics", return_value=[]), patch.object(MODULE, "prompt", return_value=""):
            MODULE.diagnostics_menu(Path(temporary))
        heading.assert_called_once_with("Diagnóstico", "Estado da instalação e do Codex-CLI")
        screen.assert_not_called()

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

    def test_codex_model_catalog_uses_the_app_server_response(self) -> None:
        model = {"id": "gpt-current", "displayName": "Modelo atual", "defaultReasoningEffort": "high", "supportedReasoningEfforts": [{"reasoningEffort": "medium"}, {"reasoningEffort": "high"}]}
        process = MagicMock()
        process.stdout = iter([json.dumps({"id": 0, "result": {}}) + "\n", json.dumps({"id": 1, "result": {"data": [model]}}) + "\n"])
        process.poll.return_value = 0
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE.subprocess, "Popen", return_value=process) as popen:
            catalog, detail = MODULE.fetch_codex_model_catalog(Path(temporary), "codex")
        self.assertEqual(catalog, [model])
        self.assertIn("1 modelo", detail)
        self.assertEqual(popen.call_args.args[0], ["codex", "app-server"])
        self.assertEqual(process.stdin.write.call_count, 3)

    def test_reasoning_effort_is_selected_from_the_catalog_compatible_options(self) -> None:
        model = {"id": "gpt-current", "defaultReasoningEffort": "high", "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "high"}]}
        self.assertEqual(MODULE.reasoning_efforts_for_model(model), ["low", "high"])
        self.assertEqual(MODULE.preferred_reasoning_effort(model), "high")

    def test_wizard_normalizes_name_and_creates_shikigami_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch("builtins.input", return_value="  Exemplo   da   Silva "), patch.object(MODULE, "configure_initial_codex", side_effect=lambda _root, data: data | {"executable": sys.executable}), patch.object(MODULE, "choose_project_directory", side_effect=lambda root, _current: str(root)), patch.object(MODULE, "choose_sandbox", return_value="workspace-write"), patch.object(MODULE, "choose_approval_policy", return_value="on-request"):
            root = Path(temporary)
            data, _message = MODULE.ensure_shikigami_configuration(root)
            installed = MODULE.initial_installation_wizard(root, data)
            self.assertIsNotNone(installed)
            self.assertEqual(installed["shikigami_identity"], "Exemplo da Silva")
            self.assertTrue(installed["shikigami_installed"])
            self.assertFalse((root / "README.md").exists())
            self.assertFalse((root / "AGENTS.md").exists())
            self.assertFalse((root / "instructions.md").exists())
            self.assertFalse((root / "shikigami").exists())

    def test_wizard_accepts_the_identity_previously_recorded_by_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch("builtins.input", return_value=""), patch.object(MODULE, "configure_initial_codex", side_effect=lambda _root, data: data | {"executable": sys.executable}), patch.object(MODULE, "choose_project_directory", side_effect=lambda root, _current: str(root)), patch.object(MODULE, "choose_sandbox", return_value="workspace-write"), patch.object(MODULE, "choose_approval_policy", return_value="on-request"):
            root = Path(temporary)
            data = MODULE.default_system() | {"shikigami_identity": "Lavelinha"}
            installed = MODULE.initial_installation_wizard(root, data)
        self.assertIsNotNone(installed)
        self.assertEqual(installed["shikigami_identity"], "Lavelinha")

    def test_telegram_token_entry_uses_the_configured_shikigami_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "configs" / "onmyoji-system.toml"; path.parent.mkdir()
            path.write_text('[shikigami]\nidentity = "Lavelinha"\n', encoding="utf-8")
            self.assertEqual(MODULE.telegram_token_entry(root), "APIs/Telegram:Lavelinha")

    def test_telegram_token_entry_replaces_the_legacy_generic_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "configs" / "onmyoji-system.toml"; path.parent.mkdir()
            path.write_text('[shikigami]\nidentity = "Lavelinha"\n', encoding="utf-8")
            self.assertEqual(MODULE.configured_telegram_token_entry(root, "APIs/Telegram:Shikigami"), "APIs/Telegram:Lavelinha")

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

    def test_codex_login_validation_uses_the_instance_codex_home(self) -> None:
        version = subprocess.CompletedProcess(["codex", "--version"], 0, "codex 1.0", "")
        status = subprocess.CompletedProcess(["codex", "login", "status"], 0, "Logged in", "")
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE.subprocess, "run", side_effect=[version, status]) as run:
            self.assertTrue(MODULE.validate_codex_login(Path(temporary), MODULE.default_system() | {"executable": "codex"})[0])
        self.assertEqual(run.call_args.args[0], ["codex", "login", "status"])
        self.assertEqual(run.call_args.kwargs["env"]["CODEX_HOME"], temporary)

    def test_initial_codex_configuration_requires_login_before_catalog(self) -> None:
        model = {"id": "gpt-current", "defaultReasoningEffort": "medium", "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]}
        events: list[str] = []
        def login(_root: Path, _data: dict[str, object]) -> tuple[bool, str]:
            events.append("login")
            return (len(events) > 1, "login")
        def catalog(_root: Path, _data: dict[str, object]) -> tuple[list[dict[str, object]], str]:
            events.append("catalog")
            return [model], "catálogo"
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "choose_codex_executable", return_value="codex"), patch.object(MODULE, "validate_codex_executable", return_value=(True, "versão")), patch.object(MODULE, "validate_codex_login", side_effect=login), patch.object(MODULE, "configure_codex_login", return_value=True), patch.object(MODULE, "codex_model_catalog", side_effect=catalog), patch.object(MODULE, "choose_codex_model", return_value=model), patch.object(MODULE, "choose_reasoning_effort", return_value="medium"), patch.object(MODULE, "validate_codex_model", return_value=(True, "validado")):
            configured = MODULE.configure_initial_codex(Path(temporary), MODULE.default_system())
        self.assertIsNotNone(configured)
        self.assertEqual(events, ["login", "login", "catalog"])

    def test_codex_model_validation_runs_an_ephemeral_read_only_call(self) -> None:
        model = subprocess.CompletedProcess(["codex", "exec"], 0, "OK", "")
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "validate_codex_selection", return_value=(True, "seleção atual")), patch.object(MODULE.subprocess, "run", return_value=model) as run:
            data = MODULE.default_system() | {"executable": "codex"}
            self.assertTrue(MODULE.validate_codex_model(Path(temporary), data)[0])
        self.assertEqual(run.call_args.args[0], ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only", "--model", "gpt-6.1-sol", "--config", 'model_reasoning_effort="medium"', "Responda exatamente: OK"])

    def test_model_validation_rejects_an_incompatible_reasoning_effort_before_execution(self) -> None:
        data = MODULE.default_system() | {"executable": "codex", "model": "gpt-6-luna", "model_reasoning_effort": "ultra"}
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "validate_codex_selection", return_value=(False, "incompatível")), patch.object(MODULE.subprocess, "run") as run:
            self.assertFalse(MODULE.validate_codex_model(Path(temporary), data)[0])
        run.assert_not_called()

    def test_diagnostics_check_all_reviewed_initial_setup_items(self) -> None:
        catalog = [{"id": "gpt-6.1-sol", "defaultReasoningEffort": "medium", "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]}]
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "validate_codex_executable", return_value=(True, "codex 1.0")), patch.object(MODULE, "validate_codex_login", return_value=(True, "login confirmado")), patch.object(MODULE, "fetch_codex_model_catalog", return_value=(catalog, "catálogo atual")), patch.object(MODULE, "validate_codex_model", return_value=(True, "modelo respondeu")):
            data = MODULE.default_system() | {"shikigami_identity": "Exemplo", "shikigami_installed": True, "executable": "codex", "project_directory": temporary}
            MODULE.system_path(Path(temporary)).parent.mkdir()
            MODULE.system_path(Path(temporary)).write_text(MODULE.render_system(data), encoding="utf-8")
            diagnostics = MODULE.shikigami_diagnostics(Path(temporary))
        self.assertEqual([(item["state"], item["label"]) for item in diagnostics], [("ok", "Nome do Shikigami"), ("ok", "Executável do Codex-CLI"), ("ok", "Login do Codex-CLI"), ("ok", "Catálogo de modelos"), ("ok", "Modelo do Codex-CLI"), ("ok", "Esforço de raciocínio"), ("ok", "Validação do modelo"), ("ok", "Pasta de trabalho"), ("ok", "Sandbox"), ("ok", "Política de aprovação"), ("ok", "Diretórios adicionais de escrita")])

    def test_diagnostics_do_not_query_models_without_login(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "validate_codex_executable", return_value=(True, "versão")), patch.object(MODULE, "validate_codex_login", return_value=(False, "sem login")), patch.object(MODULE, "fetch_codex_model_catalog") as catalog:
            data = MODULE.default_system() | {"shikigami_identity": "Exemplo", "executable": "codex", "project_directory": temporary}
            MODULE.system_path(Path(temporary)).parent.mkdir()
            MODULE.system_path(Path(temporary)).write_text(MODULE.render_system(data), encoding="utf-8")
            diagnostics = MODULE.shikigami_diagnostics(Path(temporary))
        catalog.assert_not_called()
        self.assertEqual([(item["state"], item["label"]) for item in diagnostics[2:7]], [("error", "Login do Codex-CLI"), ("pending", "Catálogo de modelos"), ("pending", "Modelo do Codex-CLI"), ("pending", "Esforço de raciocínio"), ("pending", "Validação do modelo")])

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
            with patch.object(MODULE, "validate_system", return_value=(True, "pronto")), patch("onmyoji_daemon.codex_execution.McpReconciliation.reconcile_if_pending", return_value=False), patch.object(MODULE.subprocess, "run") as run:
                MODULE.launch_codex(root, data)
            command = run.call_args.args[0]
            self.assertEqual(command[0], sys.executable)
            self.assertEqual(command[1], "--config")
            self.assertTrue(command[2].startswith('developer_instructions='))
            self.assertEqual(command[-2:], ["--add-dir", str(extra)])
            self.assertEqual(run.call_args.kwargs["cwd"], str(project))
            environment = run.call_args.kwargs["env"]
            self.assertEqual(environment["CODEX_HOME"], str(root))

    def test_telegram_setup_uses_only_the_mcp_vault_profile(self) -> None:
        source = Path(MODULE.__file__).read_text(encoding="utf-8")

        self.assertNotIn("keepass_profile", source)

    def test_launches_device_login_without_a_local_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = MODULE.default_system() | {"executable": sys.executable}
            completed = subprocess.CompletedProcess([sys.executable, "login", "--device-auth"], 0, "", "")
            with patch.object(MODULE, "validate_codex_executable", return_value=(True, "versão")), patch.object(MODULE, "validate_codex_login", return_value=(True, "login")), patch.object(MODULE.subprocess, "run", return_value=completed) as run:
                self.assertTrue(MODULE.configure_codex_login(root, data, device_auth=True))
            self.assertEqual(run.call_args.args[0], [sys.executable, "login", "--device-auth"])

    def test_launches_onmyoji_console_separately_from_native_codex(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "project"; project.mkdir()
            data = MODULE.default_system() | {"executable": sys.executable, "project_directory": str(project)}
            with patch.object(MODULE, "validate_system", return_value=(True, "pronto")), patch("onmyoji_daemon.codex_execution.runtime_ready", return_value=True), patch("onmyoji_daemon.codex_execution.daemon_python", return_value=Path(sys.executable)), patch("onmyoji_daemon.codex_execution.CodexLaunch._reconcile_mcp"), patch.object(MODULE.subprocess, "run") as run:
                MODULE.launch_onmyoji_interactive(root, data)
            command = run.call_args.args[0]
            self.assertEqual(command[:5], [sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root)])
            self.assertEqual(command[5], "interactive")

    def test_enabled_skills_are_written_to_the_local_publication_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); skill = self.fake_skill(root)
            self.assertTrue(MODULE.save_enabled(root, {"omie"}, [skill])[0])
            state_path = MODULE.skill_state_path(root)
            self.assertTrue(state_path.is_file())
            state = tomllib.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state, {"schema_version": 1, "skills": {"omie": {"enabled": True, "mode": "managed_copy", "source": "omie"}}})
            self.assertEqual(MODULE.enabled_ids(root, [skill]), {"omie"})
            self.assertTrue(MODULE.save_enabled(root, set(), [skill])[0])
            self.assertFalse(tomllib.loads(state_path.read_text(encoding="utf-8"))["skills"]["omie"]["enabled"])

    def test_missing_skill_state_is_initialized_with_managed_copy_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); skill = self.fake_skill(root)
            self.assertTrue(MODULE.ensure_skill_state(root, [skill])[0])
            state = tomllib.loads(MODULE.skill_state_path(root).read_text(encoding="utf-8"))
            self.assertEqual(state["skills"]["omie"], {"enabled": False, "mode": "managed_copy", "source": "omie"})

    def test_invalid_skill_state_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); skill = self.fake_skill(root)
            path = MODULE.skill_state_path(root); path.parent.mkdir(parents=True)
            path.write_text('[skills.omie]\nenabled = true\nmode = "link"\n', encoding="utf-8")
            ok, message = MODULE.ensure_skill_state(root, [skill])
            self.assertFalse(ok)
            self.assertIn("inválido", message)

    def test_discovers_instance_setup_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "shikigami-skills" / "barinella" / "setupSkill.py"
            script.parent.mkdir(parents=True); script.write_text("", encoding="utf-8")
            def describe(command, **_kwargs):
                identifier = Path(command[1]).parent.name
                return subprocess.CompletedProcess(command, 0, json.dumps({"id": identifier, "title": identifier.title()}), "")
            with patch.object(MODULE.subprocess, "run", side_effect=describe):
                skills = {skill.identifier: skill for skill in MODULE.discover(root)}
            self.assertEqual(set(skills), {"barinella"})

    def test_discovers_instance_skill_markdown_without_setup_script(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "shikigami-skills" / "domain-knowledge"; source.mkdir(parents=True)
            (source / "SKILL.md").write_text("# Domain knowledge\n", encoding="utf-8")
            skills = MODULE.discover(root)
            self.assertEqual(len(skills), 1)
            skill = skills[0]
            self.assertEqual(skill.identifier, "domain-knowledge")
            self.assertEqual(skill.source, source)
            self.assertIsNone(skill.setup_script)
            self.assertTrue(MODULE.save_enabled(root, {skill.identifier}, [skill])[0])
            self.assertTrue(MODULE.is_skill_enabled(root, skill, [skill]))

    def test_conflicting_instance_ids_cannot_be_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_source = root / "shikigami-skills" / "omie"; first_source.mkdir(parents=True)
            second_source = root / "shikigami-skills" / "omie-copy"; second_source.mkdir(parents=True)
            first_skill = MODULE.Skill("omie", "Omie", first_source, "")
            second_skill = MODULE.Skill("omie", "Omie", second_source, "")
            ok, message = MODULE.save_enabled(root, {"omie"}, [first_skill, second_skill])
            self.assertFalse(ok)
            self.assertIn("conflito", message)

    def test_menu_lists_only_shikigami_skills(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = MODULE.Skill("barinella", "Barinella", root / "shikigami-skills" / "barinella", "")
            output = io.StringIO()
            with patch("builtins.input", return_value="x"), patch.object(MODULE, "discover", side_effect=AssertionError("o menu não deve redescobrir skills")), patch.object(MODULE.sys, "stdout", output):
                self.assertEqual(MODULE.menu([skill], root), 0)
            self.assertIn("SKILLS DO SHIKIGAMI", output.getvalue())

    def test_skill_status_badges_keep_the_menu_column_width(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE.Ui, "enabled", False):
            root = Path(temporary)
            skill = MODULE.Skill("omie", "Omie", root / "shikigami-skills" / "omie", "")
            active = MODULE.skill_item(1, skill, MODULE.Ui.badge("ATIVA", "active"))
            inactive = MODULE.skill_item(1, skill, MODULE.Ui.badge("inativa", "inactive"))
            conflict = MODULE.skill_item(1, skill, MODULE.Ui.badge("CONFLITO", "warning"))
            self.assertEqual(len(active), 50)
            self.assertEqual(len(inactive), 50)
            self.assertEqual(len(conflict), 50)


if __name__ == "__main__":
    unittest.main()
