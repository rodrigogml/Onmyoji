from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bis10cmd


class BIS10CMDTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = tempfile.TemporaryDirectory()
        self.root = Path(self.runtime.name)
        self.jar = self.root / "BISCMD-10.0.jar"
        self.jar.write_text("jar", encoding="utf-8")
        (self.root / "libs").mkdir()
        self.config = {"client": {"jar_path": str(self.jar), "working_dir": str(self.root), "java_path": "java", "host": "127.0.0.1", "port": 8080, "locale": "pt-BR"}, "jndi": {"vault_profile": "jndi", "vault_entry_path": "jndi-entry", "username_field": "username", "password_field": "password"}, "bis": {"vault_profile": "bis", "vault_entry_path": "bis-entry", "username_field": "username", "password_field": "password"}, "execution": {"timeout_seconds": 10, "encoding": "utf-8"}}

    def tearDown(self) -> None:
        self.runtime.cleanup()

    def provider(self, value: str):
        return type("Result", (), {"returncode": 0, "stdout": json.dumps({"ok": True, "result": {"value": value}}), "stderr": ""})()

    @patch("bis10cmd.subprocess.run")
    def test_injects_four_credentials_only_in_environment(self, run) -> None:
        run.side_effect = [self.provider("jndi-user"), self.provider("jndi-secret"), self.provider("bis-user"), self.provider("bis-secret"), type("Result", (), {"returncode": 0, "stdout": "Servidor acessível\n", "stderr": ""})()]
        result = bis10cmd.run(self.config, {"version": 1, "commands": [{"name": "ping", "args": []}]})
        invocation = run.call_args_list[-1]
        self.assertEqual(invocation.args[0][-2:], ["-connect", "-ping"])
        self.assertEqual(invocation.kwargs["env"]["BISCMD_JNDI_PASSWORD"], "jndi-secret")
        self.assertEqual(invocation.kwargs["env"]["BISCMD_BIS_PASSWORD"], "bis-secret")
        self.assertNotIn("bis-secret", invocation.args[0])
        self.assertEqual(result["messages"], ["Servidor acessível"])

    def test_help_does_not_read_credentials(self) -> None:
        with patch("bis10cmd.subprocess.run", return_value=type("Result", (), {"returncode": 0, "stdout": "Ajuda\n", "stderr": ""})()) as run:
            self.assertEqual(bis10cmd.run(self.config, {"version": 1, "commands": [{"name": "help", "args": []}]}), {"messages": ["Ajuda"]})
            self.assertEqual(run.call_count, 1)

    def test_rejects_java_properties_and_unknown_commands(self) -> None:
        with self.assertRaisesRegex(bis10cmd.BIS10CMDError, "Propriedades Java"):
            bis10cmd.normalize_commands({"commands": [{"name": "ping", "args": ["-Dbiscmd.host=x"]}]})
        with self.assertRaisesRegex(bis10cmd.BIS10CMDError, "não permitido"):
            bis10cmd.normalize_commands({"commands": [{"name": "deleteAll", "args": []}]})

    def test_error_output_redacts_credentials(self) -> None:
        with patch("bis10cmd.subprocess.run") as run:
            run.side_effect = [self.provider("user"), self.provider("secret"), self.provider("bis"), self.provider("bis-secret"), type("Result", (), {"returncode": 2, "stdout": "secret\n", "stderr": "bis-secret\n"})()]
            with self.assertRaises(bis10cmd.BIS10CMDError) as error:
                bis10cmd.run(self.config, {"version": 1, "commands": [{"name": "ping", "args": []}]})
        self.assertEqual(error.exception.data, {"messages": ["[REDACTED]"], "stderr": ["[REDACTED]"]})
