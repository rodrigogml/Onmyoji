import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forward_email import Client, ForwardEmailError, load_settings


class Tests(unittest.TestCase):
    def profile(self, base="https://api.forwardemail.net/v1"):
        f = tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False, encoding="utf-8")
        f.write(f'[forward_email]\napi_base = {base}\ndomain = example.test\n[vault]\ncommand = python\nscript = vault.py\nconfig = keepass.ini\nentry_path = APIs/Forward Email\nfield = password\nauth_json = {{"mode":"windows_credential_manager","target":"test"}}\n')
        f.close(); self.addCleanup(lambda: Path(f.name).unlink(missing_ok=True)); return f.name

    def test_reads_token_without_logging_it(self):
        settings = load_settings(self.profile())
        with patch("forward_email.subprocess.run") as run:
            run.return_value = type("R", (), {"stdout": json.dumps({"ok": True, "data": {"value": "secret"}}), "returncode": 0})()
            from forward_email import read_token
            self.assertEqual(read_token(settings), "secret")
            self.assertNotIn("secret", repr(run.call_args))

    def test_requires_confirmation_for_writes(self):
        client = Client(load_settings(self.profile()), "token")
        self.assertIn("aliases.create", client.WRITE)

    def test_rejects_non_https(self):
        with self.assertRaises(ForwardEmailError): load_settings(self.profile("http://api.forwardemail.net/v1"))


if __name__ == "__main__": unittest.main()
