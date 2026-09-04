import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bis2cmd


def profile(jar):
    return {"biscmd":{"jar_path":jar,"working_dir":str(Path(jar).parent),"host":"192.168.3.64","port":8080,"java_path":"java","java_options":""},"auth":{"vault_profile":"test","vault_entry_path":"Servidores/BIS2","username_field":"username","password_field":"password"},"execution":{"timeout_seconds":10,"encoding":"utf-8"}}


class BIS2CMDTests(unittest.TestCase):
    def setUp(self):
        self.jar = tempfile.NamedTemporaryFile(suffix=".jar", delete=False)
        self.jar.close()

    def tearDown(self):
        Path(self.jar.name).unlink(missing_ok=True)

    def provider(self, value):
        return type("Result", (), {"returncode": 0, "stdout": json.dumps({"ok": True, "result": {"value": value}}), "stderr": ""})()

    @patch("bis2cmd.subprocess.run")
    def test_command_injects_credentials_only_in_environment(self, run):
        run.side_effect = [self.provider("user"), self.provider("secret"),
                            type("Result", (), {"returncode": 0, "stdout": 'BISJSON {"id":1}\nBISMETA {"complete":true}\n', "stderr": ""})()]
        result = bis2cmd.run(profile(self.jar.name), {"version": 1, "command": "companiesList", "args": []})
        self.assertEqual(result["records"], [{"id": 1}])
        invocation = run.call_args_list[2]
        self.assertNotIn("secret", invocation.args[0])
        self.assertEqual(invocation.kwargs["env"]["BISCMD_HOST"], "192.168.3.64")
        self.assertEqual(invocation.kwargs["env"]["BISCMD_PORT"], "8080")
        self.assertEqual(invocation.kwargs["env"]["BISCMD_PASSWORD"], "secret")

    def test_parse_output(self):
        result = bis2cmd.parse_output('BISJSON {"ok":true}\nBISMETA {"next_offset":5}\nMensagem\n')
        self.assertEqual(result["records"], [{"ok": True}])
        self.assertEqual(result["metadata"]["next_offset"], 5)
        self.assertEqual(result["messages"], ["Mensagem"])

    @patch("bis2cmd.subprocess.run")
    def test_invalid_version(self, run):
        with self.assertRaises(bis2cmd.BIS2CMDError) as error:
            if 2 != 1:
                raise bis2cmd.BIS2CMDError("unsupported_version", "A versão do protocolo deve ser 1.")
        self.assertEqual(error.exception.code, "unsupported_version")

    def test_missing_jar(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8") as config_file:
            config_file.write("""[profiles.example]\njar_path = "C:/missing/BISCMD.jar"\nhost = "localhost"\nport = 8080\nvault_profile = "test"\nvault_entry_path = "x"\n""")
            config_path = config_file.name
        try:
            with self.assertRaises(bis2cmd.BIS2CMDError) as error:
                bis2cmd.load_config(config_path, "example")
        finally:
            Path(config_path).unlink(missing_ok=True)
        self.assertEqual(error.exception.code, "jar_not_found")


if __name__ == "__main__":
    unittest.main()
