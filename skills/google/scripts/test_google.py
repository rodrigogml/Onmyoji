import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from google import Client, GoogleError, load_client_credentials, load_configured_client_credentials, load_settings, read_refresh_token, sanitize, write_refresh_token


class GoogleTests(unittest.TestCase):
    def profile(self, credentials_file="C:/credentials.json"):
        fd, filename = tempfile.mkstemp(suffix=".toml")
        os.close(fd)
        path = Path(filename)
        path.write_text(f'''[defaults]\nscopes = ["openid", "email", "profile", "https://mail.google.com/"]\n[profiles.example]\ncredentials_file = "{credentials_file}"\noauth_profile = "rodrigogml"\nvault_profile = "test"\nvault_entry_path = "APIs/Google:Akuma"\nclient_id_field = "username"\nclient_secret_field = "password"\nprofiles_field = "notes"\n''', encoding="utf-8")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return path

    def test_profile_validation(self):
        settings = load_settings(str(self.profile()), "example")
        self.assertEqual(settings.user_id, "me")
        self.assertEqual(settings.profile, "rodrigogml")

    def test_credentials_desktop_json(self):
        handle, filename = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        file = Path(filename)
        file.write_text(json.dumps({"installed": {"client_id": "id", "client_secret": "secret"}}), encoding="utf-8")
        self.addCleanup(lambda: file.unlink(missing_ok=True))
        self.assertEqual(load_client_credentials(str(file)), ("id", "secret"))

    def test_sanitize(self):
        self.assertEqual(sanitize({"access_token": "x", "nested": {"refresh_token": "y"}}), {"access_token": "[REDACTED]", "nested": {"refresh_token": "[REDACTED]"}})

    def test_rejects_arbitrary_operation(self):
        client = Client(load_settings(str(self.profile()), "example"), "x")
        with self.assertRaises(GoogleError):
            client.request("gmail", "arbitrary", {}, {})

    @patch("google.vault_request")
    def test_reads_shared_credentials_and_profile_token_from_vault(self, vault):
        settings = load_settings(str(self.profile(credentials_file="")), "example")

        def response(_settings, request):
            field = request["field"]
            values = {"username": "client-id", "password": "client-secret", "notes": json.dumps({"version": 1, "profiles": {"rodrigogml": {"refresh_token": "refresh-token"}}})}
            return {"ok": True, "result": {"value": values[field]}}

        vault.side_effect = response
        self.assertEqual(load_configured_client_credentials(settings), ("client-id", "client-secret"))
        self.assertEqual(read_refresh_token(settings), "refresh-token")

    @patch("google.vault_request")
    def test_writes_only_selected_profile_to_notes(self, vault):
        settings = load_settings(str(self.profile(credentials_file="")), "example")
        existing = {"version": 1, "profiles": {"other": {"refresh_token": "keep-me"}}}

        def response(_settings, request):
            if request["operation"] == "read":
                return {"ok": True, "result": {"value": json.dumps(existing)}}
            self.assertEqual(request["operation"], "edit")
            payload = json.loads(request["fields"]["notes"])
            self.assertEqual(payload["profiles"]["other"]["refresh_token"], "keep-me")
            self.assertEqual(payload["profiles"]["rodrigogml"]["refresh_token"], "new-token")
            return {"ok": True}

        vault.side_effect = response
        write_refresh_token(settings, "new-token")

    def test_requires_confirm_for_delete(self):
        client = Client(load_settings(str(self.profile()), "example"), "x")
        with self.assertRaises(GoogleError):
            client.request("drive", "files.delete", {"fileId": "x"}, {})

    def test_requires_path_id(self):
        client = Client(load_settings(str(self.profile()), "example"), "x")
        with self.assertRaises(GoogleError):
            client.request("gmail", "messages.get", {}, {})


if __name__ == "__main__":
    unittest.main()
