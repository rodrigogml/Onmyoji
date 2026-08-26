import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloudflare import Client, CloudflareError, load_settings


class Tests(unittest.TestCase):
    def profile(self, base="https://api.cloudflare.com/client/v4"):
        f = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8")
        f.write(f'''schema_version = 1
[defaults]
api_base = "{base}"
[profiles.test]
vault_profile = "vault"
vault_entry_path = "APIs/Cloudflare"
vault_field = "password"
''')
        f.close(); self.addCleanup(lambda: Path(f.name).unlink(missing_ok=True)); return f.name

    def test_profile_and_token_contract(self):
        settings = load_settings(self.profile(), "test")
        with patch("cloudflare.subprocess.run") as run:
            run.return_value = type("R", (), {"stdout": json.dumps({"ok": True, "result": {"value": "secret"}}), "returncode": 0})()
            from cloudflare import read_token
            self.assertEqual(read_token(settings), "secret")
            self.assertNotIn("secret", repr(run.call_args))

    def test_write_requires_confirmation_in_contract(self):
        client = Client(load_settings(self.profile(), "test"), "token")
        with self.assertRaises(CloudflareError): client.request("dns.records.get", {}, {})

    def test_rejects_non_https(self):
        with self.assertRaises(CloudflareError): load_settings(self.profile("http://api.cloudflare.com/client/v4"), "test")


if __name__ == "__main__": unittest.main()
