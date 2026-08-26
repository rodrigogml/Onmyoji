import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloudflare import Client, CloudflareError, load_settings


class Tests(unittest.TestCase):
    def profile(self, base="https://api.cloudflare.com/client/v4"):
        f = tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False, encoding="utf-8")
        f.write(f'[cloudflare]\napi_base = {base}\n[vault]\ncommand = python\nscript = vault.py\nconfig = keepass.ini\nentry_path = APIs/Cloudflare\nfield = password\nauth_json = {{"mode":"windows_credential_manager","target":"test"}}\n')
        f.close(); self.addCleanup(lambda: Path(f.name).unlink(missing_ok=True)); return f.name

    def test_profile_and_token_contract(self):
        settings = load_settings(self.profile())
        with patch("cloudflare.subprocess.run") as run:
            run.return_value = type("R", (), {"stdout": json.dumps({"ok": True, "data": {"value": "secret"}}), "returncode": 0})()
            from cloudflare import read_token
            self.assertEqual(read_token(settings), "secret")
            self.assertNotIn("secret", repr(run.call_args))

    def test_write_requires_confirmation_in_contract(self):
        client = Client(load_settings(self.profile()), "token")
        with self.assertRaises(CloudflareError): client.request("dns.records.get", {}, {})

    def test_rejects_non_https(self):
        with self.assertRaises(CloudflareError): load_settings(self.profile("http://api.cloudflare.com/client/v4"))


if __name__ == "__main__": unittest.main()
