import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forward_email import Client, ForwardEmailError, load_settings


class Tests(unittest.TestCase):
    def profile(self, base="https://api.forwardemail.net/v1"):
        f = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8")
        f.write(f'''schema_version = 1
[defaults]
api_base = "{base}"
[profiles.test]
domain = "example.test"
vault_profile = "vault"
vault_entry_path = "APIs/Forward Email"
vault_field = "password"
''')
        f.close(); self.addCleanup(lambda: Path(f.name).unlink(missing_ok=True)); return f.name

    def test_reads_token_without_logging_it(self):
        settings = load_settings(self.profile(), "test")
        with patch("forward_email.subprocess.run") as run:
            run.return_value = type("R", (), {"stdout": json.dumps({"ok": True, "result": {"value": "secret"}}), "returncode": 0})()
            from forward_email import read_token
            self.assertEqual(read_token(settings), "secret")
            self.assertNotIn("secret", repr(run.call_args))

    def test_requires_confirmation_for_writes(self):
        client = Client(load_settings(self.profile(), "test"), "token")
        self.assertIn("aliases.create", client.WRITE)

    def test_rejects_non_https(self):
        with self.assertRaises(ForwardEmailError): load_settings(self.profile("http://api.forwardemail.net/v1"), "test")


if __name__ == "__main__": unittest.main()
