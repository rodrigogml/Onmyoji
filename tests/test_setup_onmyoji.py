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

    def test_totp_password_configuration_creates_missing_vault_entry(self) -> None:
        resolver = MagicMock(); resolver.entry_exists.return_value = False
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "KeePassCredentialResolver", return_value=resolver), patch.object(MODULE, "prompt", return_value=""), patch.object(MODULE, "flow_choice", return_value="Informar a senha e criar esta entrada"), patch.object(MODULE.getpass, "getpass", return_value="senha-totp"):
            entry = MODULE.configure_totp_password(Path(temporary), "principal", "real", "APIs/TelegramTOTP:Example:Real")
        self.assertEqual(entry, "APIs/TelegramTOTP:Example:Real")
        resolver.save_password.assert_called_once_with("principal", "APIs/TelegramTOTP:Example:Real", "senha-totp")

    def test_diagnostics_are_a_flow_view_not_a_navigable_menu(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "flow_heading") as heading, patch.object(MODULE, "screen") as screen, patch.object(MODULE, "shikigami_diagnostics", return_value=[]), patch.object(MODULE, "prompt", return_value=""):
            MODULE.diagnostics_menu(Path(temporary))
        heading.assert_called_once_with("Diagnóstico", "Estado da instalação e do Codex-CLI")
        screen.assert_not_called()

    def test_daemon_menu_uninstalls_directly_without_utility_submenu(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
            (project / "setupDaemon.py").write_text("", encoding="utf-8")
            daemon = root / "configs" / "daemon" / "daemon.toml"; daemon.parent.mkdir(parents=True); daemon.write_text("", encoding="utf-8")
            completed = subprocess.CompletedProcess([], 0, stdout="Removido.", stderr="")
            answers = iter(["3", "desinstalar", "x"])
            with patch.object(MODULE, "prompt", side_effect=lambda _label: next(answers)), patch.object(MODULE, "screen") as screen, patch.object(MODULE.subprocess, "run", return_value=completed) as run:
                MODULE.daemon_menu(root)
        self.assertTrue(any("remove-instance" in call.args[0] for call in run.call_args_list))
        self.assertTrue(all(call.args[0] == "Daemon" for call in screen.call_args_list))

    def test_publication_menu_diagnoses_without_starting_miniapps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
            (project / "setupDaemon.py").write_text("", encoding="utf-8")
            manifest = root / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True); manifest.write_text("", encoding="utf-8")
            answers = iter(["p", "!", "2", "x", "x"])
            completed = subprocess.CompletedProcess([], 0, stdout='{"ready": false}', stderr="")
            checks = [{"state": "error", "label": "Credencial Cloudflare", "detail": "Token ausente.", "action": "Abra W e selecione uma entrada KeePass."}]
            output = io.StringIO()
            with patch.object(MODULE, "prompt", side_effect=lambda _label: next(answers)), patch.object(MODULE, "screen") as screen, patch.object(MODULE.subprocess, "run", return_value=completed) as run, patch.object(MODULE, "diagnose_publication", return_value=checks) as diagnose, patch.object(MODULE.sys, "stdout", output):
                MODULE.daemon_menu(root)
            self.assertEqual(diagnose.call_count, 2)
            self.assertTrue(all(call.args == (root,) for call in diagnose.call_args_list))
        commands = [call.args[0] for call in run.call_args_list]
        self.assertFalse(any("start" in command for command in commands))
        self.assertFalse(any("mini-apps" in command for command in commands))
        self.assertIn("Publicação externa", [call.args[0] for call in screen.call_args_list])
        self.assertIn("Próximo passo: Abra W e selecione uma entrada KeePass.", output.getvalue())

    def test_publication_installer_asks_for_directory_and_uses_async_wait(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
            (project / "setupDaemon.py").write_text("", encoding="utf-8")
            manifest = root / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True); manifest.write_text("", encoding="utf-8")
            selected = str(root / "configs" / "daemon" / "services" / "cloudflare-tunnel" / "custom-bin")
            answers = iter(["p", "3", selected, "s", "x", "x"])
            completed = subprocess.CompletedProcess([], 0, stdout='{"state": "running"}', stderr="")
            with patch.object(MODULE, "prompt", side_effect=lambda _label: next(answers)) as prompt, patch.object(MODULE, "screen"), patch.object(MODULE, "flow_choice", return_value="Instalar"), patch.object(MODULE.subprocess, "run", return_value=completed) as run, patch.object(MODULE, "configure_mcp_password_reference") as credentials, patch.object(MODULE, "inspect_cloudflared_directory", create=True, return_value={"candidates": [], "errors": [], "latest_version": None, "latest_error": "consulta indisponível", "update_available": None}):
                MODULE.daemon_menu(root)
            credentials.assert_not_called()
            self.assertFalse((manifest.parent / "services" / "cloudflare-tunnel" / "tunnel.toml").exists())
            prompts = [call.args[0] for call in prompt.call_args_list]
            self.assertTrue(any(label.startswith("Diretório de instalação [") for label in prompts))
        installs = [call.args[0] for call in run.call_args_list if "install-cloudflared" in call.args[0]]
        self.assertEqual(len(installs), 1)
        self.assertIn("--wait", installs[0])
        self.assertIn("--confirm", installs[0])
        self.assertEqual(installs[0][installs[0].index("--install-directory") + 1], selected)

    def test_publication_installer_reuses_existing_binary_when_current_outdated_or_latest_is_unavailable(self) -> None:
        for latest, update_available, latest_error in (("2026.9.1", False, None), ("2026.10.7", True, None), (None, None, "consulta oficial indisponível")):
            with self.subTest(latest=latest), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
                (project / "setupDaemon.py").write_text("", encoding="utf-8")
                manifest = root / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True); manifest.write_text("", encoding="utf-8")
                folder = root / "existing-tools"; folder.mkdir()
                executable = folder / "cloudflared.exe"; executable.write_bytes(b"existing executable")
                inspection = {"candidates": [{"executable": str(executable), "version": "2026.9.1"}], "errors": [], "latest_version": latest, "latest_error": latest_error, "update_available": update_available}
                answers = iter(["p", "3", str(folder), "x", "x"])
                events = []
                def ask(label):
                    events.append(label)
                    return next(answers)
                def inspect(*_args, **_kwargs):
                    events.append("inspect")
                    return inspection
                def choose(label, values, current=""):
                    events.append(label)
                    return "Instalar" if label == "Executável cloudflared" else "Usar o executável existente"
                def execute(arguments, **_kwargs):
                    events.append(arguments)
                    return subprocess.CompletedProcess(arguments, 0, stdout='{"state":"running"}', stderr="")
                output = io.StringIO()
                with patch.object(MODULE, "prompt", side_effect=ask) as prompts, patch.object(MODULE, "screen"), patch.object(MODULE, "flow_choice", side_effect=choose), patch.object(MODULE, "inspect_cloudflared_directory", create=True, side_effect=inspect) as inspector, patch.object(MODULE.subprocess, "run", side_effect=execute) as run, patch.object(MODULE.sys, "stdout", output):
                    MODULE.daemon_menu(root)
                commands = [call.args[0] for call in run.call_args_list]
                reuse = [command for command in commands if "use-cloudflared" in command]
                self.assertEqual(len(reuse), 1)
                self.assertEqual(reuse[0][reuse[0].index("--executable") + 1], str(executable))
                self.assertIn("--confirm", reuse[0])
                self.assertIn("--wait", reuse[0])
                self.assertFalse(any("install-cloudflared" in command or "update-cloudflared" in command for command in commands))
                self.assertFalse(any("Baixar e instalar" in call.args[0] for call in prompts.call_args_list))
                self.assertEqual(executable.read_bytes(), b"existing executable")
                self.assertEqual(inspector.call_args.args[0], str(folder))
                self.assertLess(events.index("inspect"), next(index for index, event in enumerate(events) if isinstance(event, list) and event[-2:] == ["start", "cloudflare-tunnel"]))
                self.assertIn("2026.9.1", output.getvalue())
                if latest_error:
                    self.assertIn("indisponível", output.getvalue())

    def test_publication_installer_inspects_before_confirmation_and_pins_the_selected_update(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
            (project / "setupDaemon.py").write_text("", encoding="utf-8")
            manifest = root / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True); manifest.write_text("", encoding="utf-8")
            folder = root / "existing-tools"; folder.mkdir()
            executable = folder / "cloudflared.exe"; executable.write_bytes(b"existing executable")
            inspection = {"candidates": [{"executable": str(executable), "version": "2026.9.1"}], "errors": [], "latest_version": "2026.10.7", "latest_error": None, "update_available": True}
            answers = iter(["p", "3", str(folder), "s", "x", "x"])
            events = []
            def ask(label):
                events.append(label)
                return next(answers)
            def inspect(*_args, **_kwargs):
                events.append("inspect")
                return inspection
            def choose(label, values, current=""):
                return "Instalar" if label == "Executável cloudflared" else "Instalar nova versão 2026.10.7"
            completed = subprocess.CompletedProcess([], 0, stdout='{"state":"running"}', stderr="")
            with patch.object(MODULE, "prompt", side_effect=ask), patch.object(MODULE, "screen"), patch.object(MODULE, "flow_choice", side_effect=choose), patch.object(MODULE, "inspect_cloudflared_directory", create=True, side_effect=inspect), patch.object(MODULE.subprocess, "run", return_value=completed) as run:
                MODULE.daemon_menu(root)
            commands = [call.args[0] for call in run.call_args_list]
            installations = [command for command in commands if "install-cloudflared" in command or "update-cloudflared" in command]
            self.assertEqual(len(installations), 1)
            self.assertEqual(installations[0][installations[0].index("--version") + 1], "2026.10.7")
            self.assertEqual(installations[0][installations[0].index("--install-directory") + 1], str(folder))
            self.assertFalse(any("use-cloudflared" in command for command in commands))
            self.assertEqual(executable.read_bytes(), b"existing executable")
            self.assertLess(events.index("inspect"), next(index for index, value in enumerate(events) if value.startswith("Baixar e instalar")))

    def test_publication_installer_requires_selection_when_multiple_binaries_are_found(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
            (project / "setupDaemon.py").write_text("", encoding="utf-8")
            manifest = root / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True); manifest.write_text("", encoding="utf-8")
            folder = root / "tools"; folder.mkdir()
            first = folder / "cloudflared.exe"; second = folder / "cloudflared-windows-amd64.exe"
            first.write_bytes(b"first"); second.write_bytes(b"second")
            inspection = {"candidates": [{"executable": str(first), "version": "2026.9.1"}, {"executable": str(second), "version": "2026.10.7"}], "errors": [], "latest_version": "2026.10.7", "latest_error": None, "update_available": True}
            answers = iter(["p", "3", str(folder), "x", "x"])
            selections = []
            def choose(label, values, current=""):
                if label == "Executável cloudflared": return "Instalar"
                if label == "O que fazer com o cloudflared": return "Usar o executável existente"
                selections.append(values)
                return values[-1]
            completed = subprocess.CompletedProcess([], 0, stdout='{"state":"running"}', stderr="")
            with patch.object(MODULE, "prompt", side_effect=lambda _label: next(answers)), patch.object(MODULE, "screen"), patch.object(MODULE, "flow_choice", side_effect=choose), patch.object(MODULE, "inspect_cloudflared_directory", create=True, return_value=inspection), patch.object(MODULE.subprocess, "run", return_value=completed) as run:
                MODULE.daemon_menu(root)
            self.assertEqual(len(selections), 1)
            self.assertEqual(len(selections[0]), 2)
            reuse = next(call.args[0] for call in run.call_args_list if "use-cloudflared" in call.args[0])
            self.assertEqual(reuse[reuse.index("--executable") + 1], str(second))

    def test_publication_installer_cancel_after_existing_binary_inspection_does_not_mutate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
            (project / "setupDaemon.py").write_text("", encoding="utf-8")
            manifest = root / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True); manifest.write_text("", encoding="utf-8")
            folder = root / "tools"; folder.mkdir()
            executable = folder / "cloudflared.exe"; executable.write_bytes(b"existing")
            inspection = {"candidates": [{"executable": str(executable), "version": "2026.9.1"}], "errors": [], "latest_version": "2026.10.7", "latest_error": None, "update_available": True}
            answers = iter(["p", "3", str(folder), "x", "x"])
            def choose(label, values, current=""):
                return "Instalar" if label == "Executável cloudflared" else None
            completed = subprocess.CompletedProcess([], 0, stdout="[]", stderr="")
            with patch.object(MODULE, "prompt", side_effect=lambda _label: next(answers)), patch.object(MODULE, "screen"), patch.object(MODULE, "flow_choice", side_effect=choose), patch.object(MODULE, "inspect_cloudflared_directory", create=True, return_value=inspection), patch.object(MODULE.subprocess, "run", return_value=completed) as run:
                MODULE.daemon_menu(root)
            self.assertFalse(any(any(action in call.args[0] for action in ("start", "use-cloudflared", "install-cloudflared", "update-cloudflared")) for call in run.call_args_list))
            self.assertEqual(executable.read_bytes(), b"existing")

    def test_publication_installer_explains_invalid_binary_before_install_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
            (project / "setupDaemon.py").write_text("", encoding="utf-8")
            manifest = root / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True); manifest.write_text("", encoding="utf-8")
            selected = str(root / "tools")
            inspection = {"candidates": [], "errors": [{"executable": str(root / "tools" / "cloudflared.exe"), "code": "invalid_executable", "message": "O executável não confirmou uma versão válida."}], "latest_version": "2026.10.7", "latest_error": None, "update_available": None}
            answers = iter(["p", "3", selected, "n", "x", "x"])
            completed = subprocess.CompletedProcess([], 0, stdout="[]", stderr="")
            output = io.StringIO()
            with patch.object(MODULE, "prompt", side_effect=lambda _label: next(answers)), patch.object(MODULE, "screen"), patch.object(MODULE, "flow_choice", return_value="Instalar"), patch.object(MODULE, "inspect_cloudflared_directory", create=True, return_value=inspection), patch.object(MODULE.subprocess, "run", return_value=completed) as run, patch.object(MODULE.sys, "stdout", output):
                MODULE.daemon_menu(root)
            self.assertIn("não confirmou uma versão válida", output.getvalue())
            self.assertFalse(any(any(action in call.args[0] for action in ("start", "use-cloudflared", "install-cloudflared", "update-cloudflared")) for call in run.call_args_list))

    def test_publication_configuration_validates_ids_and_stores_only_the_vault_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
            (project / "setupDaemon.py").write_text("", encoding="utf-8")
            manifest = root / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True); manifest.write_text("", encoding="utf-8")
            events = []
            answers = iter(["p", "1", "this-is-a-token-not-an-id", "A" * 32, "", "b" * 32, "https://tsuki.example.com", "Tsuki.Example.Com", "", "x", "x"])
            def ask(label):
                events.append(label)
                return next(answers)
            def choose_credential(*_args):
                events.append("credential")
                return "principal", "Tsuki/Serviço/CloudflareTunnel:APIToken"
            completed = subprocess.CompletedProcess([], 0, stdout='{"configured": true}', stderr="")
            with patch.object(MODULE, "prompt", side_effect=ask) as prompts, patch.object(MODULE, "screen") as screens, patch.object(MODULE, "configure_mcp_password_reference", side_effect=choose_credential), patch.object(MODULE.subprocess, "run", return_value=completed) as run:
                MODULE.daemon_menu(root)
            account_prompts = [call.args[0] for call in prompts.call_args_list if call.args[0].startswith("Account ID")]
            zone_prompts = [call.args[0] for call in prompts.call_args_list if call.args[0].startswith("Zone ID")]
        self.assertEqual(len(account_prompts), 2)
        self.assertEqual(len(zone_prompts), 2)
        self.assertLess(events.index("credential"), next(index for index, value in enumerate(events) if value.startswith("Account ID")))
        configure = [call.args[0] for call in run.call_args_list if "configure" in call.args[0]]
        self.assertEqual(len(configure), 1)
        values = json.loads(configure[0][configure[0].index("--values") + 1])
        self.assertEqual(values, {"account_id": "a" * 32, "zone_id": "b" * 32, "hostname": "tsuki.example.com", "credential_profile": "principal", "credential_entry_path": "Tsuki/Serviço/CloudflareTunnel:APIToken"})
        self.assertTrue(all(call.args[0] in {"Daemon", "Publicação externa"} for call in screens.call_args_list))

    def test_publication_installer_does_not_install_when_wrapper_reports_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
            (project / "setupDaemon.py").write_text("", encoding="utf-8")
            manifest = root / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True); manifest.write_text("", encoding="utf-8")
            selected = str(root / "configs" / "daemon" / "services" / "cloudflare-tunnel" / "bin")
            answers = iter(["p", "3", selected, "s", "x", "x"])
            def execute(arguments, **_kwargs):
                response = {"state": "failed", "last_error": "O runtime não possui a dependência necessária."} if arguments[-2:] == ["start", "cloudflare-tunnel"] else []
                return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(response), stderr="")
            output = io.StringIO()
            with patch.object(MODULE, "prompt", side_effect=lambda _label: next(answers)), patch.object(MODULE, "screen"), patch.object(MODULE, "flow_choice", return_value="Instalar"), patch.object(MODULE.subprocess, "run", side_effect=execute) as run, patch.object(MODULE.sys, "stdout", output), patch.object(MODULE, "inspect_cloudflared_directory", create=True, return_value={"candidates": [], "errors": [], "latest_version": None, "latest_error": "consulta indisponível", "update_available": None}):
                MODULE.daemon_menu(root)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(any(command[-2:] == ["start", "cloudflare-tunnel"] for command in commands))
        self.assertFalse(any("install-cloudflared" in command for command in commands))
        self.assertIn("O runtime não possui a dependência necessária.", output.getvalue())

    def test_publication_provision_waits_for_origin_confirmation_and_does_not_send_after_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
            (project / "setupDaemon.py").write_text("", encoding="utf-8")
            manifest = root / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True); manifest.write_text("", encoding="utf-8")
            tunnel = manifest.parent / "services" / "cloudflare-tunnel" / "tunnel.toml"; tunnel.parent.mkdir(parents=True)
            tunnel.write_text('schema_version = 1\naccount_id = "' + "a" * 32 + '"\nzone_id = "' + "b" * 32 + '"\nhostname = "tsuki.example.com"\ncredential_profile = "principal"\ncredential_entry_path = "Tsuki/Serviço/CloudflareTunnel:APIToken"\n', encoding="utf-8")
            answers = iter(["p", "4", "x", "x"])
            def execute(arguments, **_kwargs):
                if arguments[-1] == "--version":
                    return subprocess.CompletedProcess(arguments, 0, stdout="cloudflared version 2026.11.0", stderr="")
                response = {"activated": False, "ca_file": ""} if arguments[-2:] == ["cloudflare-tunnel", "status"] else {"state": "running"}
                return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(response), stderr="")
            output = io.StringIO()
            with patch.object(MODULE, "prompt", side_effect=lambda _label: next(answers)), patch.object(MODULE, "screen"), patch.object(MODULE.shutil, "which", return_value="cloudflared"), patch.object(MODULE.subprocess, "run", side_effect=execute) as run, patch.object(MODULE.time, "monotonic", side_effect=[100, 100, 111]), patch.object(MODULE.time, "sleep"), patch.object(MODULE.sys, "stdout", output):
                MODULE.daemon_menu(root)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(any(command[-2:] == ["start", "gateway"] for command in commands))
        self.assertTrue(any(command[-2:] == ["start", "cloudflare-tunnel"] for command in commands))
        self.assertTrue(any(command[-2:] == ["cloudflare-tunnel", "status"] for command in commands))
        self.assertFalse(any("provision" in command for command in commands))
        self.assertIn("Nenhuma solicitação de provisionamento foi enviada", output.getvalue())

    def test_publication_configuration_cancels_at_an_id_without_saving_the_tunnel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
            (project / "setupDaemon.py").write_text("", encoding="utf-8")
            manifest = root / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True); manifest.write_text("", encoding="utf-8")
            answers = iter(["p", "1", "x", "x", "x"])
            completed = subprocess.CompletedProcess([], 0, stdout="{}", stderr="")
            with patch.object(MODULE, "prompt", side_effect=lambda _label: next(answers)), patch.object(MODULE, "screen"), patch.object(MODULE, "configure_mcp_password_reference", return_value=("principal", "Tsuki/Serviço/CloudflareTunnel:APIToken")), patch.object(MODULE.subprocess, "run", return_value=completed) as run:
                MODULE.daemon_menu(root)
        self.assertFalse(any("configure" in call.args[0] or "provision" in call.args[0] for call in run.call_args_list))

    def test_publication_existing_tunnel_is_inspected_before_confirmed_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
            (project / "setupDaemon.py").write_text("", encoding="utf-8")
            manifest = root / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True); manifest.write_text("", encoding="utf-8")
            tunnel = manifest.parent / "services" / "cloudflare-tunnel" / "tunnel.toml"; tunnel.parent.mkdir(parents=True)
            tunnel.write_text('schema_version = 1\naccount_id = "' + "a" * 32 + '"\nzone_id = "' + "b" * 32 + '"\nhostname = "tsuki.example.com"\ncredential_profile = "principal"\ncredential_entry_path = "Tsuki/Serviço/CloudflareTunnel:APIToken"\n', encoding="utf-8")
            preview = {"status": "existing", "hostname": "tsuki.example.com", "tunnel_id": "known-tunnel", "tunnel_name": "onmyoji-mini-apps", "current_origin": "https://localhost:39391", "new_origin": "https://localhost:39390", "preview_id": "a" * 32, "message": "Publicação existente localizada na Cloudflare."}
            answers = iter(["p", "4", "s", "x", "x"])
            events = []
            def ask(label):
                events.append(label)
                return next(answers)
            def execute(arguments, **kwargs):
                events.append(arguments)
                if arguments[-1] == "--version":
                    return subprocess.CompletedProcess(arguments, 0, stdout="cloudflared version 2026.11.0", stderr="")
                if "inspect-publication" in arguments:
                    self.assertEqual(kwargs.get("stdout"), subprocess.PIPE)
                    self.assertFalse(kwargs.get("capture_output", False))
                    response = {"state": "succeeded", "result": preview}
                elif arguments[-2:] == ["cloudflare-tunnel", "status"]:
                    response = {"activated": True, "ca_file": str(root / "gateway-ca.pem"), "gateway_url": "https://localhost:39390"}
                else:
                    response = {"state": "running"}
                return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(response), stderr="")
            output = io.StringIO()
            with patch.object(MODULE, "prompt", side_effect=ask), patch.object(MODULE, "screen"), patch.object(MODULE.shutil, "which", return_value="cloudflared"), patch.object(MODULE.subprocess, "run", side_effect=execute) as run, patch.object(MODULE.sys, "stdout", output):
                MODULE.daemon_menu(root)
            commands = [call.args[0] for call in run.call_args_list]
            adoption = [command for command in commands if "adopt-publication" in command]
            self.assertEqual(len(adoption), 1)
            self.assertEqual(adoption[0][adoption[0].index("--preview-id") + 1], "a" * 32)
            self.assertIn("--confirm", adoption[0]); self.assertIn("--wait", adoption[0])
            self.assertFalse(any("provision" in command for command in commands))
            inspection = next(index for index, item in enumerate(events) if isinstance(item, list) and "inspect-publication" in item)
            confirmation = next(index for index, item in enumerate(events) if isinstance(item, str) and item.startswith("Reutilizar este Tunnel"))
            self.assertLess(inspection, confirmation)
            self.assertIn("onmyoji-mini-apps", output.getvalue())
            self.assertIn("https://localhost:39391", output.getvalue())
            self.assertIn("https://localhost:39390", output.getvalue())
            self.assertFalse((manifest.parent / "services" / "mini-apps" / "tunnel.toml").exists())

    def test_publication_existing_tunnel_decline_or_cancel_never_changes_remote_state(self) -> None:
        for answer in ("", "n", "x"):
            with self.subTest(answer=answer), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
                (project / "setupDaemon.py").write_text("", encoding="utf-8")
                manifest = root / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True); manifest.write_text("", encoding="utf-8")
                tunnel = manifest.parent / "services" / "cloudflare-tunnel" / "tunnel.toml"; tunnel.parent.mkdir(parents=True)
                tunnel.write_text('schema_version = 1\naccount_id = "' + "a" * 32 + '"\nzone_id = "' + "b" * 32 + '"\nhostname = "tsuki.example.com"\ncredential_profile = "principal"\ncredential_entry_path = "Tsuki/Serviço/CloudflareTunnel:APIToken"\n', encoding="utf-8")
                answers = iter(["p", "4", answer, "x", "x"])
                def execute(arguments, **_kwargs):
                    if arguments[-1] == "--version":
                        return subprocess.CompletedProcess(arguments, 0, stdout="cloudflared version 2026.11.0", stderr="")
                    if "inspect-publication" in arguments:
                        response = {"state": "succeeded", "result": {"status": "existing", "hostname": "tsuki.example.com", "tunnel_id": "known-tunnel", "tunnel_name": "old-tunnel", "current_origin": "https://localhost:39391", "new_origin": "https://localhost:39390", "preview_id": "a" * 32}}
                    elif arguments[-2:] == ["cloudflare-tunnel", "status"]:
                        response = {"activated": True, "ca_file": str(root / "gateway-ca.pem")}
                    else:
                        response = {"state": "running"}
                    return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(response), stderr="")
                with patch.object(MODULE, "prompt", side_effect=lambda _label: next(answers)), patch.object(MODULE, "screen"), patch.object(MODULE.shutil, "which", return_value="cloudflared"), patch.object(MODULE.subprocess, "run", side_effect=execute) as run:
                    MODULE.daemon_menu(root)
                commands = [call.args[0] for call in run.call_args_list]
                self.assertTrue(any("inspect-publication" in command for command in commands))
                self.assertFalse(any(action in command for command in commands for action in ("adopt-publication", "provision")))

    def test_publication_inspection_failure_explains_permission_and_stops_before_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
            (project / "setupDaemon.py").write_text("", encoding="utf-8")
            manifest = root / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True); manifest.write_text("", encoding="utf-8")
            tunnel = manifest.parent / "services" / "cloudflare-tunnel" / "tunnel.toml"; tunnel.parent.mkdir(parents=True)
            tunnel.write_text('schema_version = 1\naccount_id = "' + "a" * 32 + '"\nzone_id = "' + "b" * 32 + '"\nhostname = "tsuki.example.com"\ncredential_profile = "principal"\ncredential_entry_path = "Tsuki/Serviço/CloudflareTunnel:APIToken"\n', encoding="utf-8")
            answers = iter(["p", "4", "x", "x"])
            def execute(arguments, **_kwargs):
                if arguments[-1] == "--version":
                    return subprocess.CompletedProcess(arguments, 0, stdout="cloudflared version 2026.11.0", stderr="")
                if "inspect-publication" in arguments:
                    response = {"state": "failed", "progress": "consultando DNS", "error_code": "cloudflare_http_error", "error": "Cloudflare retornou HTTP 403. Confira as permissões DNS do API Token."}
                    return subprocess.CompletedProcess(arguments, 2, stdout=json.dumps(response), stderr="")
                response = {"activated": True, "ca_file": str(root / "gateway-ca.pem")} if arguments[-2:] == ["cloudflare-tunnel", "status"] else {"state": "running"}
                return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(response), stderr="")
            output = io.StringIO()
            with patch.object(MODULE, "prompt", side_effect=lambda _label: next(answers)) as prompts, patch.object(MODULE, "screen"), patch.object(MODULE.shutil, "which", return_value="cloudflared"), patch.object(MODULE.subprocess, "run", side_effect=execute) as run, patch.object(MODULE.sys, "stdout", output):
                MODULE.daemon_menu(root)
            commands = [call.args[0] for call in run.call_args_list]
            self.assertFalse(any(action in command for command in commands for action in ("adopt-publication", "provision")))
            self.assertFalse(any("[s/N]" in call.args[0] for call in prompts.call_args_list))
            self.assertIn("403", output.getvalue()); self.assertIn("permissões DNS", output.getvalue())

    def test_publication_diagnosis_pending_transport_is_not_reported_ready(self) -> None:
        for checks, expected_ready in (([{"state": "pending", "label": "Conexão Cloudflare", "detail": "Conexão não confirmada."}], False), ([{"state": "ok", "label": "Conexão Cloudflare", "detail": "Conectado."}, {"state": "pending", "label": "Mini Apps (opcional)", "detail": "Não configurado."}], True)):
            with self.subTest(checks=checks), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
                (project / "setupDaemon.py").write_text("", encoding="utf-8")
                manifest = root / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True); manifest.write_text("", encoding="utf-8")
                answers = iter(["p", "!", "x", "x"])
                with patch.object(MODULE, "prompt", side_effect=lambda _label: next(answers)), patch.object(MODULE, "screen"), patch.object(MODULE.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="{}", stderr="")), patch.object(MODULE, "diagnose_publication", return_value=checks), patch.object(MODULE, "result") as result:
                    MODULE.daemon_menu(root)
                self.assertEqual(result.call_args.args[0], expected_ready)

    def test_publication_restore_selects_backup_and_sends_confirmed_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
            (project / "setupDaemon.py").write_text("", encoding="utf-8")
            manifest = root / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True); manifest.write_text("", encoding="utf-8")
            directory = manifest.parent / "services" / "cloudflare-tunnel" / "publication-recovery"; directory.mkdir(parents=True)
            for identifier, state in (("a" * 32, "applied"), ("b" * 32, "failed"), ("c" * 32, "prepared"), ("d" * 32, "rolled_back")):
                (directory / (identifier + ".json")).write_text(json.dumps({"state": state}), encoding="utf-8")
            (directory / ("e" * 32 + ".json")).write_text("{invalid", encoding="utf-8")
            (directory / "invalid-name.json").write_text('{"state":"applied"}', encoding="utf-8")
            answers = iter(["p", "9", "2", "s", "x", "x"])
            completed = subprocess.CompletedProcess([], 0, stdout='{"state":"running"}', stderr="")
            output = io.StringIO()
            with patch.object(MODULE, "prompt", side_effect=lambda _label: next(answers)) as prompts, patch.object(MODULE, "screen"), patch.object(MODULE.subprocess, "run", return_value=completed) as run, patch.object(MODULE.sys, "stdout", output):
                MODULE.daemon_menu(root)
            rollback = [call.args[0] for call in run.call_args_list if "rollback-publication" in call.args[0]]
            self.assertEqual(len(rollback), 1)
            self.assertEqual(rollback[0][rollback[0].index("--preview-id") + 1], "a" * 32)
            self.assertIn("--confirm", rollback[0]); self.assertIn("--wait", rollback[0])
            self.assertTrue(any(call.args[0] == "Opção [1]: " for call in prompts.call_args_list))
            self.assertIn("a" * 32 + " · applied", output.getvalue())
            self.assertIn("b" * 32 + " · failed", output.getvalue())
            self.assertNotIn("c" * 32, output.getvalue()); self.assertNotIn("d" * 32, output.getvalue())
            self.assertNotIn("invalid-name", output.getvalue())

    def test_publication_restore_cancel_at_selection_or_confirmation_never_starts_or_mutates(self) -> None:
        for responses in (("x",), ("", ""), ("", "n"), ("", "x")):
            with self.subTest(responses=responses), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
                (project / "setupDaemon.py").write_text("", encoding="utf-8")
                manifest = root / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True); manifest.write_text("", encoding="utf-8")
                directory = manifest.parent / "services" / "cloudflare-tunnel" / "publication-recovery"; directory.mkdir(parents=True)
                (directory / ("a" * 32 + ".json")).write_text('{"state":"applied"}', encoding="utf-8")
                answers = iter(["p", "9", *responses, "x", "x"])
                completed = subprocess.CompletedProcess([], 0, stdout='{"state":"running"}', stderr="")
                with patch.object(MODULE, "prompt", side_effect=lambda _label: next(answers)), patch.object(MODULE, "screen"), patch.object(MODULE.subprocess, "run", return_value=completed) as run:
                    MODULE.daemon_menu(root)
                self.assertFalse(any(action in call.args[0] for call in run.call_args_list for action in ("start", "rollback-publication")))

    def test_publication_restore_explains_when_no_applied_backup_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
            (project / "setupDaemon.py").write_text("", encoding="utf-8")
            manifest = root / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True); manifest.write_text("", encoding="utf-8")
            answers = iter(["p", "9", "x", "x"])
            output = io.StringIO()
            with patch.object(MODULE, "prompt", side_effect=lambda _label: next(answers)), patch.object(MODULE, "screen"), patch.object(MODULE.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="{}", stderr="")) as run, patch.object(MODULE, "flow_choice") as choice, patch.object(MODULE.sys, "stdout", output):
                MODULE.daemon_menu(root)
            choice.assert_not_called()
            self.assertIn("Não há backup", output.getvalue())
            self.assertFalse(any(action in call.args[0] for call in run.call_args_list for action in ("start", "rollback-publication")))

    def test_publication_restore_failure_retains_backup_and_reports_incomplete_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
            (project / "setupDaemon.py").write_text("", encoding="utf-8")
            manifest = root / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True); manifest.write_text("", encoding="utf-8")
            directory = manifest.parent / "services" / "cloudflare-tunnel" / "publication-recovery"; directory.mkdir(parents=True)
            backup = directory / ("a" * 32 + ".json"); backup.write_text('{"state":"applied"}', encoding="utf-8")
            answers = iter(["p", "9", "", "s", "x", "x"])
            def execute(arguments, **_kwargs):
                if "rollback-publication" in arguments:
                    return subprocess.CompletedProcess(arguments, 2, stdout='{"state":"failed","error_code":"remote_changed"}', stderr="")
                return subprocess.CompletedProcess(arguments, 0, stdout='{"state":"running"}', stderr="")
            output = io.StringIO()
            with patch.object(MODULE, "prompt", side_effect=lambda _label: next(answers)), patch.object(MODULE, "screen"), patch.object(MODULE.subprocess, "run", side_effect=execute), patch.object(MODULE.sys, "stdout", output):
                MODULE.daemon_menu(root)
            self.assertIn("restauração não foi concluída", output.getvalue())
            self.assertIn("backup foi preservado", output.getvalue())
            self.assertEqual(json.loads(backup.read_text(encoding="utf-8"))["state"], "applied")

    def test_publication_wizard_prepares_gateway_and_requests_remote_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
            (project / "setupDaemon.py").write_text("", encoding="utf-8")
            manifest = root / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True); manifest.write_text("", encoding="utf-8")
            answers = iter(["p", "w", "a" * 32, "b" * 32, "tsuki.example.com", "", "", "s", "", "x", "x"])
            def execute(arguments, **_kwargs):
                if arguments[-1] == "list-services":
                    response = [{"name": "gateway"}, {"name": "cloudflare-tunnel"}]
                elif arguments[-2:] == ["gateway", "status"]:
                    response = {"http_port": 39390, "ready": True, "public_origin": "https://tsuki.example.com"}
                elif arguments[-2:] == ["cloudflare-tunnel", "status"]:
                    response = {"activated": True, "ca_file": str(root / "gateway-ca.pem"), "gateway_url": "https://localhost:39390", "configured": True, "provisioned": True, "running": True, "connected": True, "ingress_ready": True}
                elif "publication-test" in arguments:
                    response = {"test_id": "a" * 32, "url": "https://tsuki.example.com/internal/publication-test/test", "expires_in_seconds": 120, "state": "waiting"} if "start" in arguments else {"state": "confirmed"}
                elif "inspect-publication" in arguments:
                    response = {"state": "succeeded", "result": {"status": "new", "hostname": "tsuki.example.com", "new_origin": "https://localhost:39390", "message": "Nenhuma publicação existente encontrada."}}
                elif "configure" in arguments:
                    values = json.loads(arguments[arguments.index("--values") + 1])
                    tunnel = manifest.parent / "services" / "cloudflare-tunnel" / "tunnel.toml"; tunnel.parent.mkdir(parents=True)
                    tunnel.write_text("schema_version = 1\n" + "\n".join(f"{key} = {json.dumps(value)}" for key, value in values.items()), encoding="utf-8")
                    response = {"configured": True}
                elif arguments[-1] == "--version":
                    return subprocess.CompletedProcess(arguments, 0, stdout="cloudflared version 2026.11.0", stderr="")
                else:
                    response = {"state": "running"}
                return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(response), stderr="")
            with patch.object(MODULE, "prompt", side_effect=lambda _label: next(answers)) as prompts, patch.object(MODULE, "screen") as screens, patch.object(MODULE, "configure_mcp_password_reference", return_value=("principal", "Tsuki/Serviço/CloudflareTunnel:APIToken")), patch.object(MODULE.shutil, "which", return_value="cloudflared"), patch.object(MODULE.subprocess, "run", side_effect=execute) as run, patch.object(MODULE, "diagnose_publication", return_value=[{"state": "ok", "label": "Conexão", "detail": "Pronta"}]) as diagnose:
                MODULE.daemon_menu(root)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(any(command[-2:] == ["start", "gateway"] for command in commands))
        provision = [command for command in commands if "provision" in command]
        self.assertEqual(len(provision), 1)
        self.assertIn("--confirm", provision[0])
        self.assertIn("--wait", provision[0])
        self.assertTrue(any(call.args[0] == "Provisionar hostname, ingress e DNS na Cloudflare? [s/N]: " for call in prompts.call_args_list))
        self.assertTrue(any(call.args[0] == "Porta HTTPS local do Gateway [39390]: " for call in prompts.call_args_list))
        self.assertTrue(any(command[-2:] == ["enable", "gateway"] for command in commands))
        self.assertTrue(any(command[-2:] == ["enable", "cloudflare-tunnel"] for command in commands))
        public_tests = [index for index, command in enumerate(commands) if command[-3:] == ["publication-test", "start", "--confirm"]]
        self.assertEqual(len(public_tests), 1)
        self.assertGreater(public_tests[0], next(index for index, command in enumerate(commands) if command[-2:] == ["enable", "cloudflare-tunnel"]))
        self.assertFalse(any("mini-apps" in command for command in commands))
        self.assertEqual(diagnose.call_count, 1)
        self.assertTrue(all(call.args[0] in {"Daemon", "Publicação externa"} for call in screens.call_args_list))

    def test_publication_wizard_does_not_provision_when_confirmation_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
            (project / "setupDaemon.py").write_text("", encoding="utf-8")
            manifest = root / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True); manifest.write_text("", encoding="utf-8")
            tunnel = manifest.parent / "services" / "cloudflare-tunnel" / "tunnel.toml"; tunnel.parent.mkdir(parents=True)
            tunnel.write_text('schema_version = 1\naccount_id = "' + "a" * 32 + '"\nzone_id = "' + "b" * 32 + '"\nhostname = "tsuki.example.com"\ncredential_profile = "principal"\ncredential_entry_path = "Tsuki/Serviço/CloudflareTunnel:APIToken"\n', encoding="utf-8")
            answers = iter(["p", "w", "", "", "", "", "", "", "x", "x"])
            def execute(arguments, **_kwargs):
                if arguments[-1] == "list-services":
                    response = [{"name": "gateway"}, {"name": "cloudflare-tunnel"}]
                elif arguments[-2:] == ["gateway", "status"]:
                    response = {"http_port": 39390}
                elif arguments[-2:] == ["cloudflare-tunnel", "status"]:
                    response = {"activated": True, "ca_file": str(root / "gateway-ca.pem"), "gateway_url": "https://localhost:39390"}
                elif "inspect-publication" in arguments:
                    response = {"state": "succeeded", "result": {"status": "new", "hostname": "tsuki.example.com", "new_origin": "https://localhost:39390", "message": "Nenhuma publicação existente encontrada."}}
                elif arguments[-1] == "--version":
                    return subprocess.CompletedProcess(arguments, 0, stdout="cloudflared version 2026.11.0", stderr="")
                else:
                    response = {"state": "running"}
                return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(response), stderr="")
            with patch.object(MODULE, "prompt", side_effect=lambda _label: next(answers)), patch.object(MODULE, "screen"), patch.object(MODULE, "configure_mcp_password_reference", return_value=("principal", "Tsuki/Serviço/CloudflareTunnel:APIToken")), patch.object(MODULE.shutil, "which", return_value="cloudflared"), patch.object(MODULE.subprocess, "run", side_effect=execute) as run, patch.object(MODULE, "diagnose_publication") as diagnose:
                MODULE.daemon_menu(root)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(any("configure" in command for command in commands))
        self.assertFalse(any("provision" in command or "enable" in command for command in commands))
        diagnose.assert_not_called()

    def test_publication_wizard_stops_when_configuration_cannot_be_saved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root / "onmyoji-daemon"; project.mkdir()
            (project / "setupDaemon.py").write_text("", encoding="utf-8")
            manifest = root / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True); manifest.write_text("", encoding="utf-8")
            answers = iter(["p", "w", "a" * 32, "b" * 32, "tsuki.example.com", "", "x", "x"])
            def execute(arguments, **_kwargs):
                if "configure" in arguments:
                    return subprocess.CompletedProcess(arguments, 2, stdout="", stderr="Erro: referência KeePass indisponível.")
                return subprocess.CompletedProcess(arguments, 0, stdout='[{"name":"gateway"},{"name":"cloudflare-tunnel"}]', stderr="")
            output = io.StringIO()
            with patch.object(MODULE, "prompt", side_effect=lambda _label: next(answers)), patch.object(MODULE, "screen"), patch.object(MODULE, "configure_mcp_password_reference", return_value=("principal", "Tsuki/Serviço/CloudflareTunnel:APIToken")), patch.object(MODULE.subprocess, "run", side_effect=execute) as run, patch.object(MODULE.sys, "stdout", output), patch.object(MODULE, "diagnose_publication") as diagnose:
                MODULE.daemon_menu(root)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertFalse(any("provision" in command or "install-cloudflared" in command or "start" in command for command in commands))
        self.assertIn("referência KeePass indisponível", output.getvalue())
        diagnose.assert_not_called()

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
        self.assertEqual(popen.call_args.kwargs["cwd"], str(Path(temporary) / "tmp" / "codex-cli"))
        self.assertEqual(process.stdin.write.call_count, 3)

    def test_reasoning_effort_is_selected_from_the_catalog_compatible_options(self) -> None:
        model = {"id": "gpt-current", "defaultReasoningEffort": "high", "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "high"}]}
        self.assertEqual(MODULE.reasoning_efforts_for_model(model), ["low", "high"])
        self.assertEqual(MODULE.preferred_reasoning_effort(model), "high")

    def test_wizard_normalizes_name_and_creates_shikigami_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch("builtins.input", side_effect=["  Exemplo   da   Silva ", "1", ""]), patch.object(MODULE, "validate_setup_python", return_value=(True, "ok", False)), patch.object(MODULE, "configure_initial_codex", side_effect=lambda _root, data: data | {"executable": sys.executable}), patch.object(MODULE, "choose_project_directory", side_effect=lambda root, _current: str(root)), patch.object(MODULE, "choose_sandbox", return_value="workspace-write"), patch.object(MODULE, "choose_approval_policy", return_value="on-request"):
            root = Path(temporary)
            data, _message = MODULE.ensure_shikigami_configuration(root)
            installed = MODULE.initial_installation_wizard(root, data)
            self.assertIsNotNone(installed)
            self.assertEqual(installed["shikigami_identity"], "Exemplo da Silva")
            self.assertEqual(installed["python_mode"], "isolated")
            self.assertEqual(installed["python_base_executable"], sys.executable)
            self.assertTrue(installed["shikigami_installed"])
            self.assertFalse((root / "README.md").exists())
            self.assertFalse((root / "AGENTS.md").exists())
            self.assertFalse((root / "instructions.md").exists())
            self.assertFalse((root / "shikigami").exists())

    def test_wizard_accepts_the_identity_previously_recorded_by_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch("builtins.input", return_value=""), patch.object(MODULE, "validate_setup_python", return_value=(True, "ok", False)), patch.object(MODULE, "configure_initial_codex", side_effect=lambda _root, data: data | {"executable": sys.executable}), patch.object(MODULE, "choose_project_directory", side_effect=lambda root, _current: str(root)), patch.object(MODULE, "choose_sandbox", return_value="workspace-write"), patch.object(MODULE, "choose_approval_policy", return_value="on-request"):
            root = Path(temporary)
            data = MODULE.default_system() | {"shikigami_identity": "Lavelinha"}
            installed = MODULE.initial_installation_wizard(root, data)
        self.assertIsNotNone(installed)
        self.assertEqual(installed["shikigami_identity"], "Lavelinha")

    def test_dedicated_child_profile_requires_the_registered_account_and_marker(self) -> None:
        profile = {"mode": "dedicated", "account": "shikigami-tsuki", "home": "/home/shikigami-tsuki"}
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "load_execution_profile", return_value=profile), patch.object(MODULE, "linux_current_user", return_value="shikigami-tsuki"), patch.dict(MODULE.os.environ, {MODULE.DEDICATED_SETUP_ENVIRONMENT: "1"}, clear=False):
            self.assertEqual(MODULE.dedicated_child_profile(Path(temporary)), profile)
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "load_execution_profile", return_value=profile), patch.object(MODULE, "linux_current_user", return_value="outro"), patch.dict(MODULE.os.environ, {MODULE.DEDICATED_SETUP_ENVIRONMENT: "1"}, clear=False):
            self.assertEqual(MODULE.dedicated_child_profile(Path(temporary)), {})

    def test_git_setup_migrates_legacy_origin_and_tracks_the_instance_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "instance"; own = Path(temporary) / "own.git"; root.mkdir()
            subprocess.run(["git", "init", "--bare", str(own)], check=True, capture_output=True)
            for arguments in (["init", "-b", "main"], ["config", "user.name", "Teste"], ["config", "user.email", "teste@example.test"]):
                subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True)
            (root / "base.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "base.txt"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Base"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "remote", "add", "origin", "https://github.com/rodrigogml/Onmyoji.git"], cwd=root, check=True, capture_output=True)
            for key, values in {
                "branch.main.remote": ["upstream", "legacy"],
                "branch.main.merge": ["refs/heads/upstream", "refs/heads/legacy"],
                "branch.main.pushRemote": ["upstream", "legacy"],
                "pull.rebase": ["true", "merges"],
                "pull.ff": ["only", "false"],
            }.items():
                for value in values:
                    subprocess.run(["git", "config", "--local", "--add", key, value], cwd=root, check=True, capture_output=True)

            with patch.object(MODULE, "prompt", side_effect=["", "", str(own)]):
                configured, detail = MODULE.configure_git_repository(root)

            self.assertTrue(configured, detail)
            upstream = subprocess.run(["git", "remote", "get-url", "upstream"], cwd=root, text=True, capture_output=True, check=True).stdout.strip()
            self.assertEqual(MODULE._git_location(upstream), MODULE._git_location("https://github.com/rodrigogml/Onmyoji.git"))
            self.assertEqual(subprocess.run(["git", "remote", "get-url", "origin"], cwd=root, text=True, capture_output=True, check=True).stdout.strip(), str(own))
            self.assertEqual(subprocess.run(["git", "config", "--local", "--get-all", "branch.main.remote"], cwd=root, text=True, capture_output=True, check=True).stdout.splitlines(), ["origin"])
            self.assertEqual(subprocess.run(["git", "config", "--local", "--get-all", "branch.main.merge"], cwd=root, text=True, capture_output=True, check=True).stdout.splitlines(), ["refs/heads/main"])
            self.assertEqual(subprocess.run(["git", "config", "--local", "--get-all", "branch.main.pushRemote"], cwd=root, text=True, capture_output=True, check=True).stdout.splitlines(), ["origin"])
            self.assertEqual(subprocess.run(["git", "config", "--local", "--get-all", "pull.rebase"], cwd=root, text=True, capture_output=True, check=True).stdout.splitlines(), ["false"])
            self.assertEqual(subprocess.run(["git", "config", "--local", "--get-all", "pull.ff"], cwd=root, text=True, capture_output=True, check=True).stdout.splitlines(), ["true"])
            self.assertEqual(subprocess.run(["git", "config", "--get", "remote.pushDefault"], cwd=root, text=True, capture_output=True, check=True).stdout.strip(), "origin")
            attributes = (root / ".git" / "info" / "attributes").read_text(encoding="utf-8")
            self.assertIn("instructions.md merge=shikigami-local", attributes)

    def test_git_sync_merges_upstream_main_into_the_instance_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); upstream = base / "upstream.git"; own = base / "own.git"; seed = base / "seed"; root = base / "instance"
            subprocess.run(["git", "init", "--bare", str(upstream)], check=True, capture_output=True)
            subprocess.run(["git", "init", "--bare", str(own)], check=True, capture_output=True)
            seed.mkdir(); subprocess.run(["git", "init", "-b", "main"], cwd=seed, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Teste"], cwd=seed, check=True); subprocess.run(["git", "config", "user.email", "teste@example.test"], cwd=seed, check=True)
            (seed / "base.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "base.txt"], cwd=seed, check=True); subprocess.run(["git", "commit", "-m", "Base"], cwd=seed, check=True, capture_output=True)
            subprocess.run(["git", "remote", "add", "origin", str(upstream)], cwd=seed, check=True); subprocess.run(["git", "push", "-u", "origin", "main"], cwd=seed, check=True, capture_output=True)
            subprocess.run(["git", "clone", "--origin", "upstream", "--branch", "main", str(upstream), str(root)], check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Teste"], cwd=root, check=True); subprocess.run(["git", "config", "user.email", "teste@example.test"], cwd=root, check=True)
            subprocess.run(["git", "remote", "add", "origin", str(own)], cwd=root, check=True); subprocess.run(["git", "push", "-u", "origin", "main"], cwd=root, check=True, capture_output=True)
            (seed / "upstream.txt").write_text("nova base\n", encoding="utf-8")
            subprocess.run(["git", "add", "upstream.txt"], cwd=seed, check=True); subprocess.run(["git", "commit", "-m", "Atualiza base"], cwd=seed, check=True, capture_output=True); subprocess.run(["git", "push", "origin", "main"], cwd=seed, check=True, capture_output=True)

            synchronized, detail = MODULE.sync_git_remote(root, "upstream")

            self.assertTrue(synchronized, detail)
            self.assertEqual((root / "upstream.txt").read_text(encoding="utf-8"), "nova base\n")
            parents = subprocess.run(["git", "rev-list", "--parents", "-n", "1", "HEAD"], cwd=root, text=True, capture_output=True, check=True).stdout.split()
            self.assertEqual(len(parents), 3)

    def test_execution_diagnostics_runs_as_the_validated_dedicated_account(self) -> None:
        profile = {"mode": "dedicated", "account": "shikigami-tsuki", "home": "/home/shikigami-tsuki", "setup_python": "/usr/bin/python3", "validated": True}
        expected = [{"state": "ok", "label": "Identidade", "detail": "shikigami-tsuki"}]
        completed = subprocess.CompletedProcess(["sudo"], 0, json.dumps(expected), "")
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "load_execution_profile", return_value=profile), patch.object(MODULE, "linux_current_user", return_value="instalador"), patch.object(MODULE, "account_command", return_value=["sudo", "child"]) as delegated, patch.object(MODULE.subprocess, "run", return_value=completed):
            diagnostics = MODULE.execution_diagnostics(Path(temporary))
        self.assertEqual(diagnostics, expected)
        self.assertIn("--dedicated-checks", delegated.call_args.args[2])

    def test_mcp_menu_uses_the_instance_runtime_python(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); executable = root / "venv" / "bin" / "python"; executable.parent.mkdir(parents=True); executable.touch()
            completed = subprocess.CompletedProcess([], 0)
            with patch.object(MODULE, "mcp_runtime_ready", return_value=True), patch.object(MODULE, "mcp_runtime_python", return_value=executable), patch.object(MODULE.subprocess, "run", return_value=completed) as run:
                self.assertTrue(MODULE.launch_mcp_menu(root))
        run.assert_called_once_with([str(executable), "-B", str(root / "onmyoji-daemon" / "setupMcp.py")], check=False)

    def test_mcp_menu_offers_to_repair_an_incomplete_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); executable = root / "venv" / "bin" / "python"; executable.parent.mkdir(parents=True); executable.touch()
            completed = subprocess.CompletedProcess([], 0)
            with patch.object(MODULE, "mcp_runtime_ready", return_value=False), patch.object(MODULE, "prompt", return_value=""), patch.object(MODULE, "ensure_mcp_runtime", return_value="Runtime MCP preparado.") as ensure, patch.object(MODULE, "mcp_runtime_python", return_value=executable), patch.object(MODULE.subprocess, "run", return_value=completed):
                self.assertTrue(MODULE.launch_mcp_menu(root))
        ensure.assert_called_once_with(root, progress=MODULE.note)

    def test_dedicated_preparation_persists_identity_before_provision_and_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "instance"; workspace = Path(temporary) / "work"; root.mkdir(); workspace.mkdir()
            observed = {}
            def launch(_root, profile):
                observed.update(profile)
                saved = MODULE.load_execution_profile(root)
                saved["validated"] = True
                MODULE.save_execution_profile(root, saved)
                return True
            with patch.object(MODULE, "configure_installer_git_access", return_value=(True, "ok")), patch.object(MODULE, "provision_account", return_value=(True, "ok", Path("/home/shikigami-tsuki"))) as provision, patch.object(MODULE, "prepare_dedicated_prerequisites") as prerequisites, patch.object(MODULE, "launch_dedicated_setup", side_effect=launch):
                self.assertTrue(MODULE.prepare_dedicated_environment(root, "Tsuki", workspace, "shikigami-tsuki", "instalador", True))
            provision.assert_called_once()
            prerequisites.assert_called_once()
            self.assertEqual(observed["account"], "shikigami-tsuki")
            self.assertEqual(observed["workspace"], str(workspace.resolve()))
            self.assertFalse(observed["validated"])

    def test_codex_cli_install_uses_the_current_identity_local_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "instance"; home = Path(temporary) / "home"; root.mkdir(); home.mkdir()
            npm = Path(temporary) / "npm"; npm.write_text("", encoding="utf-8")
            completed = subprocess.CompletedProcess([str(npm)], 0, "", "")
            with patch.object(MODULE.Path, "home", return_value=home), patch.object(MODULE.shutil, "which", return_value=str(npm)), patch.object(MODULE, "prompt", return_value=""), patch.object(MODULE, "linux_current_user", return_value="shikigami-tsuki"), patch.object(MODULE, "validate_codex_executable", return_value=(True, "ok")), patch.object(MODULE.subprocess, "run", return_value=completed) as executed:
                executable = MODULE.install_codex_cli(root)
            self.assertEqual(executable, str((home / ".local" / "bin" / "codex").absolute()))
            command = executed.call_args.args[0]
            self.assertEqual(command[:4], [str(npm), "install", "--global", "--prefix"])
            self.assertEqual(command[-1], "@openai/codex@latest")

    def test_codex_executable_choice_offers_assisted_installation_on_linux(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE.os, "name", "posix"), patch.object(MODULE, "discover_codex_executables", return_value=[]), patch.object(MODULE, "flow_choice", return_value="Instalar ou atualizar o Codex CLI nesta conta"), patch.object(MODULE, "install_codex_cli", return_value="/home/shikigami/.local/bin/codex") as install:
            selected = MODULE.choose_codex_executable("", flow=True, root=Path(temporary))
        self.assertEqual(selected, "/home/shikigami/.local/bin/codex")
        install.assert_called_once()

    def test_linux_package_install_requires_confirmation_and_uses_sudo(self) -> None:
        tools = {"apt-get": "/usr/bin/apt-get", "sudo": "/usr/bin/sudo"}
        completed = subprocess.CompletedProcess([], 0, "", "")
        with patch.object(MODULE, "linux_tool", side_effect=lambda name: tools.get(name)), patch.object(MODULE.os, "geteuid", return_value=1000, create=True), patch.object(MODULE, "prompt", return_value=""), patch.object(MODULE.subprocess, "run", return_value=completed) as executed:
            installed = MODULE.install_linux_packages("Node.js/npm", {"apt-get": ["nodejs", "npm"]})
        self.assertTrue(installed)
        self.assertEqual(executed.call_args_list[0].args[0], ["/usr/bin/sudo", "--", "/usr/bin/apt-get", "update"])
        self.assertEqual(executed.call_args_list[1].args[0], ["/usr/bin/sudo", "--", "/usr/bin/apt-get", "install", "-y", "nodejs", "npm"])

    def test_dedicated_prerequisites_prepare_npm_before_entering_the_service_account(self) -> None:
        profile = {"account": "shikigami-tsuki", "home": "/home/shikigami-tsuki"}
        versions = [(False, "codex ausente"), (False, "npm ausente"), (True, "10.0.0")]
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "dedicated_command_version", side_effect=versions), patch.object(MODULE, "dedicated_command_check", return_value=(True, "Python 3.13")), patch.object(MODULE, "install_linux_packages", return_value=True) as install:
            MODULE.prepare_dedicated_prerequisites(Path(temporary), profile)
        install.assert_called_once()
        self.assertIn("Node.js/npm", install.call_args.args[0])

    def test_python_setup_validation_distinguishes_a_missing_venv_module(self) -> None:
        version = subprocess.CompletedProcess(["python"], 0, "3.13.1\n", "")
        venv = subprocess.CompletedProcess(["python"], 1, "", "No module named venv")
        with patch.object(MODULE.subprocess, "run", side_effect=[version, venv]):
            valid, detail, missing_venv = MODULE.validate_setup_python(Path("/usr/bin/python3.13"), True)
        self.assertFalse(valid)
        self.assertTrue(missing_venv)
        self.assertIn("venv", detail)

    def test_telegram_token_entry_uses_the_configured_shikigami_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "configs" / "onmyoji-system.toml"; path.parent.mkdir()
            path.write_text('[shikigami]\nidentity = "Lavelinha"\n', encoding="utf-8")
            self.assertEqual(MODULE.telegram_token_entry(root), "Lavelinha/Serviço/Telegram:BotToken")

    def test_telegram_token_entry_replaces_the_legacy_generic_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "configs" / "onmyoji-system.toml"; path.parent.mkdir()
            path.write_text('[shikigami]\nidentity = "Lavelinha"\n', encoding="utf-8")
            self.assertEqual(MODULE.configured_telegram_token_entry(root, "APIs/Telegram:Shikigami"), "Lavelinha/Serviço/Telegram:BotToken")
            self.assertEqual(MODULE.configured_telegram_token_entry(root, "Lavelinha/Serviço/Telegram:default:Bot"), "Lavelinha/Serviço/Telegram:BotToken")

    def test_telegram_totp_entries_use_the_configured_shikigami_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "configs" / "onmyoji-system.toml"; path.parent.mkdir()
            path.write_text('[shikigami]\nidentity = "Lavelinha"\n', encoding="utf-8")
            self.assertEqual(MODULE.telegram_totp_entry(root, "real"), "Lavelinha/Serviço/Telegram:TOTP:Real")
            self.assertEqual(MODULE.telegram_totp_entry(root, "fake"), "Lavelinha/Serviço/Telegram:TOTP:Fake")
            self.assertEqual(MODULE.configured_telegram_totp_entry(root, "real", "APIs/TelegramTOTP:Lavelinha:Real"), "Lavelinha/Serviço/Telegram:TOTP:Real")
            self.assertEqual(MODULE.configured_telegram_totp_entry(root, "fake", "Lavelinha/Serviço/Telegram:default:TOTP:Fake"), "Lavelinha/Serviço/Telegram:TOTP:Fake")
            self.assertEqual(MODULE.configured_telegram_totp_entry(root, "real", "Outro/Telegram:Senha:Real"), "Outro/Telegram:Senha:Real")

    def test_vault_entry_path_uses_only_instance_and_layer_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "configs" / "onmyoji-system.toml"; path.parent.mkdir()
            path.write_text('[shikigami]\nidentity = "Lavelinha"\n', encoding="utf-8")
            self.assertEqual(MODULE.vault_entry_path(root, "MCP", "Todoist", "default", "Token"), "Lavelinha/MCP/Todoist:default:Token")
            self.assertEqual(MODULE.vault_entry_path(root, "MCP", "AWS", "default", "Credentials"), "Lavelinha/MCP/AWS:default:Credentials")
            self.assertEqual(MODULE.vault_entry_path(root, "MCP", "SSH", "default", "PrivateKey"), "Lavelinha/MCP/SSH:default:PrivateKey")
            self.assertEqual(MODULE.vault_entry_path(root, "MCP", "BTG", "sandbox", "default:OAuth"), "Lavelinha/MCP/BTG:sandbox:default:OAuth")

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
        self.assertEqual(run.call_args.kwargs["cwd"], str(Path(temporary) / "tmp" / "codex-cli"))

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
        self.assertEqual(run.call_args.kwargs["cwd"], str(Path(temporary) / "tmp" / "codex-cli"))

    def test_model_validation_rejects_an_incompatible_reasoning_effort_before_execution(self) -> None:
        data = MODULE.default_system() | {"executable": "codex", "model": "gpt-6-luna", "model_reasoning_effort": "ultra"}
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "validate_codex_selection", return_value=(False, "incompatível")), patch.object(MODULE.subprocess, "run") as run:
            self.assertFalse(MODULE.validate_codex_model(Path(temporary), data)[0])
        run.assert_not_called()

    def test_diagnostics_check_all_reviewed_initial_setup_items(self) -> None:
        catalog = [{"id": "gpt-6.1-sol", "defaultReasoningEffort": "medium", "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]}]
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "validate_codex_executable", return_value=(True, "codex 1.0")), patch.object(MODULE, "validate_codex_login", return_value=(True, "login confirmado")), patch.object(MODULE, "fetch_codex_model_catalog", return_value=(catalog, "catálogo atual")), patch.object(MODULE, "validate_codex_model", return_value=(True, "modelo respondeu")):
            data = MODULE.default_system() | {"shikigami_identity": "Exemplo", "shikigami_installed": True, "python_base_executable": sys.executable, "executable": "codex", "project_directory": temporary}
            MODULE.system_path(Path(temporary)).parent.mkdir()
            MODULE.system_path(Path(temporary)).write_text(MODULE.render_system(data), encoding="utf-8")
            diagnostics = MODULE.shikigami_diagnostics(Path(temporary))
        self.assertEqual([(item["state"], item["label"]) for item in diagnostics], [("pending", "Ambiente Python"), ("ok", "Nome do Shikigami"), ("ok", "Executável do Codex-CLI"), ("ok", "Login do Codex-CLI"), ("ok", "Catálogo de modelos"), ("ok", "Modelo do Codex-CLI"), ("ok", "Esforço de raciocínio"), ("ok", "Validação do modelo"), ("ok", "Pasta de trabalho"), ("ok", "Sandbox"), ("ok", "Política de aprovação"), ("ok", "Diretórios adicionais de escrita")])

    def test_diagnostics_do_not_query_models_without_login(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "validate_codex_executable", return_value=(True, "versão")), patch.object(MODULE, "validate_codex_login", return_value=(False, "sem login")), patch.object(MODULE, "fetch_codex_model_catalog") as catalog:
            data = MODULE.default_system() | {"shikigami_identity": "Exemplo", "python_base_executable": sys.executable, "executable": "codex", "project_directory": temporary}
            MODULE.system_path(Path(temporary)).parent.mkdir()
            MODULE.system_path(Path(temporary)).write_text(MODULE.render_system(data), encoding="utf-8")
            diagnostics = MODULE.shikigami_diagnostics(Path(temporary))
        catalog.assert_not_called()
        self.assertEqual([(item["state"], item["label"]) for item in diagnostics[3:8]], [("error", "Login do Codex-CLI"), ("pending", "Catálogo de modelos"), ("pending", "Modelo do Codex-CLI"), ("pending", "Esforço de raciocínio"), ("pending", "Validação do modelo")])

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
            self.assertEqual(run.call_args.kwargs["cwd"], str(root / "tmp" / "codex-cli"))

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

    def test_main_menu_exposes_python_before_codex_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            with patch("builtins.input", return_value="x"), patch.object(MODULE.sys, "stdout", output):
                self.assertEqual(MODULE.menu([], Path(temporary)), 0)
            rendered = output.getvalue()
        self.assertLess(rendered.index("P.  Python e bibliotecas"), rendered.index("C.  Codex-CLI"))

    def test_python_component_catalog_identifies_consumers(self) -> None:
        components = {name: (group, consumers) for name, group, consumers in MODULE.PYTHON_COMPONENTS}
        self.assertEqual(components["IMAPClient"], ("Base MCP", "MCP E-mail"))
        self.assertIn("Telegram", components["aiohttp"][1])
        self.assertIn("MCP OCR avançado", components["paddleocr"][1])

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

    def test_codex_menu_exposes_optional_agent_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(MODULE, "load_system", return_value=MODULE.default_system()), patch.object(MODULE, "validate_system", return_value=(False, "pendente")), patch.object(MODULE, "prompt", side_effect=["9", "x"]), patch.object(MODULE, "agent_profiles_menu") as profiles, patch.object(MODULE, "screen"):
                MODULE.codex_menu(root)
            profiles.assert_called_once_with(root)

    def test_agent_profiles_empty_is_valid_and_does_not_create_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); output = io.StringIO()
            with patch.object(MODULE, "prompt", return_value="x"), patch.object(MODULE, "screen"), patch.object(MODULE.sys, "stdout", output):
                MODULE.agent_profiles_menu(root)
            self.assertIn("Nenhum perfil específico", output.getvalue())
            self.assertFalse((root / "shikigami-agents").exists())
            self.assertFalse((root / "configs" / "shikigami" / "agents.toml").exists())

    def test_agent_profile_cannot_be_enabled_without_g4(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); workspace = root / "workspace"; workspace.mkdir()
            source = root / "shikigami-agents" / "reviewer.toml"; source.parent.mkdir()
            source.write_text('name="reviewer"\ndescription="Revisão"\ndeveloper_instructions="Confira"\n', encoding="utf-8")
            data = MODULE.default_system() | {"executable": "codex", "project_directory": str(workspace)}
            output = io.StringIO()
            with patch.object(MODULE, "load_system", return_value=data), patch.object(MODULE, "load_profile_capability", return_value={"supported": False, "reason": "g4_unverified"}), patch.object(MODULE, "prompt", side_effect=["1", "x"]), patch.object(MODULE, "screen"), patch.object(MODULE.sys, "stdout", output):
                MODULE.agent_profiles_menu(root)
            self.assertIn("não foi habilitado", output.getvalue())
            self.assertFalse((root / "configs" / "shikigami" / "agents.toml").exists())

    def test_agent_profile_setup_saves_only_local_enablement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); workspace = root / "workspace"; workspace.mkdir()
            source = root / "shikigami-agents" / "reviewer.toml"; source.parent.mkdir()
            source.write_text('name="reviewer"\ndescription="Revisão"\ndeveloper_instructions="Confira"\n', encoding="utf-8")
            data = MODULE.default_system() | {"executable": "codex", "project_directory": str(workspace)}
            with patch.object(MODULE, "load_system", return_value=data), patch.object(MODULE, "load_profile_capability", return_value={"supported": True, "reason": "supported"}), patch.object(MODULE, "prompt", side_effect=["1", "s", "x"]), patch.object(MODULE, "screen"):
                MODULE.agent_profiles_menu(root)
            self.assertTrue(MODULE.ShikigamiAgentMaterializer(root).configuration()["reviewer"]["enabled"])
            self.assertFalse((workspace / ".codex").exists())
            self.assertFalse((root / "config.toml").exists())

    def test_gateway_execution_flow_defaults_and_no_new_screen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); capability = MagicMock(); capability.supports.return_value = False; capability.reason.return_value = "g1_unverified"
            completed = subprocess.CompletedProcess([], 0, "Salvo", "")
            with patch.object(MODULE.CapabilitySet, "from_instance", return_value=capability), patch.object(MODULE, "flow_choice", return_value="Enfileirar mensagens"), patch.object(MODULE, "prompt", side_effect=["", "", ""]), patch.object(MODULE, "screen") as screen, patch.object(MODULE.subprocess, "run", return_value=completed) as run:
                self.assertTrue(MODULE.configure_gateway_execution(root))
            screen.assert_not_called()
            args = run.call_args.args[0]
            self.assertEqual(args[args.index("--busy-input-policy") + 1], "queue")
            self.assertEqual(args[args.index("--preparation-workers") + 1], "4")
            self.assertEqual(args[args.index("--interrupt-grace-seconds") + 1], "15")
            self.assertEqual(args[args.index("--post-turn-results") + 1], "false")

    def test_gateway_execution_reports_fallback_without_claiming_support(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); capability = MagicMock(); capability.supports.return_value = False; capability.reason.return_value = "g1_unverified"
            output = io.StringIO(); completed = subprocess.CompletedProcess([], 0, "Salvo", "")
            with patch.object(MODULE.CapabilitySet, "from_instance", return_value=capability), patch.object(MODULE, "flow_choice", return_value="Direcionar o turno ativo quando suportado"), patch.object(MODULE, "prompt", side_effect=["6", "20", "s"]), patch.object(MODULE.sys, "stdout", output), patch.object(MODULE.subprocess, "run", return_value=completed) as run:
                self.assertTrue(MODULE.configure_gateway_execution(root))
            args = run.call_args.args[0]
            self.assertEqual(args[args.index("--busy-input-policy") + 1], "steer")
            self.assertEqual(args[args.index("--post-turn-results") + 1], "true")
            self.assertIn("política efetiva permanece fila", output.getvalue())
            self.assertIn("permanece desativada efetivamente", output.getvalue())

    def test_gateway_execution_cancel_and_invalid_input_do_not_save(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); capability = MagicMock(); capability.supports.return_value = False; capability.reason.return_value = "g1_unverified"
            for answers in (["x"], ["0"], ["17"], ["abc"], ["", "301"], ["", "", "talvez"]):
                with self.subTest(answers=answers), patch.object(MODULE.CapabilitySet, "from_instance", return_value=capability), patch.object(MODULE, "flow_choice", return_value="Enfileirar mensagens"), patch.object(MODULE, "prompt", side_effect=answers), patch.object(MODULE.subprocess, "run") as run:
                    self.assertFalse(MODULE.configure_gateway_execution(root))
                    run.assert_not_called()

    def test_gateway_execution_cancel_selection_does_not_save(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "flow_choice", return_value=None), patch.object(MODULE.subprocess, "run") as run:
            self.assertFalse(MODULE.configure_gateway_execution(Path(temporary)))
        run.assert_not_called()

    def test_gateway_execution_failure_does_not_echo_private_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            with patch.object(MODULE, "flow_choice", return_value="Enfileirar mensagens"), patch.object(MODULE, "prompt", side_effect=["", "", ""]), patch.object(MODULE.sys, "stdout", output), patch.object(MODULE.subprocess, "run", side_effect=OSError("token=segredo")):
                self.assertFalse(MODULE.configure_gateway_execution(Path(temporary)))
            self.assertNotIn("segredo", output.getvalue())


if __name__ == "__main__":
    unittest.main()
def test_agent_test_offers_repair_and_retries_missing_module(monkeypatch):
    from types import SimpleNamespace
    responses = iter([SimpleNamespace(returncode=1, stdout="Teste falhou: No module named 'jsonschema'", stderr=""), SimpleNamespace(returncode=0, stdout="Agente validado", stderr="")])
    repairs = []
    monkeypatch.setattr(MODULE, "prompt", lambda _text: "")
    assert MODULE.test_agent_with_runtime_repair(lambda: next(responses), lambda: repairs.append(True) or True)
    assert repairs == [True]


def test_agent_test_does_not_repair_unrelated_failure(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(MODULE, "prompt", lambda _text: (_ for _ in ()).throw(AssertionError("Não deve pedir reparação")))
    assert not MODULE.test_agent_with_runtime_repair(lambda: SimpleNamespace(returncode=1, stdout="Falha de autenticação", stderr=""), lambda: True)


def test_agent_test_respects_declined_repair(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(MODULE, "prompt", lambda _text: "n")
    assert not MODULE.test_agent_with_runtime_repair(lambda: SimpleNamespace(returncode=1, stdout="No module named 'jsonschema'", stderr=""), lambda: (_ for _ in ()).throw(AssertionError("Não deve reparar")))


def test_telegram_agent_test_uses_instance_python_under_dedicated_account(tmp_path, monkeypatch):
    project = tmp_path / "onmyoji-daemon"
    project.mkdir()
    (project / "setupDaemon.py").touch()
    manifest = tmp_path / "configs" / "daemon" / "daemon.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("", encoding="utf-8")
    runtime = tmp_path / "venv" / "bin" / "python"
    profile = {"mode": "dedicated", "validated": True, "account": "isolado", "home": str(tmp_path), "setup_python": "/usr/bin/python3"}
    delegated = []
    answers = iter(["t", "3", "1", "x", "x", "x"])
    monkeypatch.setattr(MODULE, "load_execution_profile", lambda _root: profile)
    monkeypatch.setattr(MODULE, "linux_current_user", lambda: "operador")
    monkeypatch.setattr(MODULE.python_runtime, "python", lambda _root: runtime)
    monkeypatch.setattr(MODULE, "prompt", lambda _label: next(answers))
    monkeypatch.setattr(MODULE, "account_command", lambda account, home, args, env: delegated.append((account, args)) or args)
    monkeypatch.setattr(MODULE.subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="[]", stderr=""))

    MODULE.daemon_menu(tmp_path)

    tests = [(account, args) for account, args in delegated if "test-agent" in args]
    assert len(tests) == 1
    assert tests[0][0] == "isolado"
    assert tests[0][1][0] == str(runtime)


def test_telegram_connection_test_uses_instance_python_under_dedicated_account(tmp_path, monkeypatch):
    project = tmp_path / "onmyoji-daemon"
    project.mkdir()
    (project / "setupDaemon.py").touch()
    manifest = tmp_path / "configs" / "daemon" / "daemon.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("", encoding="utf-8")
    runtime = tmp_path / "venv" / "bin" / "python"
    profile = {"mode": "dedicated", "validated": True, "account": "isolado", "home": str(tmp_path), "setup_python": "/usr/bin/python3"}
    delegated = []
    answers = iter(["t", "2", "2", "x", "x", "x"])
    monkeypatch.setattr(MODULE, "load_execution_profile", lambda _root: profile)
    monkeypatch.setattr(MODULE, "linux_current_user", lambda: "operador")
    monkeypatch.setattr(MODULE.python_runtime, "python", lambda _root: runtime)
    monkeypatch.setattr(MODULE, "prompt", lambda _label: next(answers))
    monkeypatch.setattr(MODULE, "account_command", lambda account, home, args, env: delegated.append((account, args)) or args)
    monkeypatch.setattr(MODULE.subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="[]", stderr=""))

    MODULE.daemon_menu(tmp_path)

    tests = [(account, args) for account, args in delegated if "test-telegram" in args]
    assert len(tests) == 1
    assert tests[0][0] == "isolado"
    assert tests[0][1][0] == str(runtime)


def test_daemon_wizard_restarts_system_service_with_sudo_confirmation(tmp_path, monkeypatch):
    from types import SimpleNamespace
    project = tmp_path / "onmyoji-daemon"
    project.mkdir()
    (project / "setupDaemon.py").touch()
    daemon = tmp_path / "configs" / "daemon"
    daemon.mkdir(parents=True)
    (daemon / "daemon.toml").write_text('[app_server]\nenabled = true\n', encoding="utf-8")
    (daemon / "service.json").write_text('{"scope": "system"}', encoding="utf-8")
    real_os = MODULE.os
    class LinuxOs:
        name = "posix"
        def geteuid(self): return 1000
        def __getattr__(self, name): return getattr(real_os, name)
    calls = []
    answers = iter(["w", "n", "s", "", "s", "s", "x"])
    def run(args, **_kwargs):
        calls.append(args)
        output = '[{"label":"Runtime Python do daemon","state":"ok"}]' if "daemon-validation-json" in args else "ok"
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")
    monkeypatch.setattr(MODULE, "os", LinuxOs())
    monkeypatch.setattr(MODULE, "load_execution_profile", lambda _root: {})
    monkeypatch.setattr(MODULE, "execution_diagnostics", lambda *_args: [])
    monkeypatch.setattr(MODULE.python_runtime, "python", lambda _root: tmp_path / "venv" / "bin" / "python")
    monkeypatch.setattr(MODULE, "prompt", lambda _label: next(answers))
    monkeypatch.setattr(MODULE.subprocess, "run", run)

    MODULE.daemon_menu(tmp_path)

    restart = [args for args in calls if "service-restart" in args]
    assert len(restart) == 1
    assert "--use-sudo" in restart[0]
