import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from notion import Client, NotionError, load_settings, read_token, sanitize


class NotionTests(unittest.TestCase):
    def profile(self, api_base="https://api.notion.com", version="2026-03-11"):
        handle, filename = tempfile.mkstemp(suffix=".toml")
        os.close(handle)
        path = Path(filename)
        path.write_text(f'''[defaults]\napi_base = "{api_base}"\nnotion_version = "{version}"\n[profiles.example]\nvault_profile = "test"\nvault_entry_path = "APIs/Notion"\nvault_field = "password"\n''', encoding="utf-8")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return path

    def test_validates_host_and_version(self):
        with self.assertRaises(NotionError):
            load_settings(str(self.profile("https://example.test")), "example")
        with self.assertRaises(NotionError):
            load_settings(str(self.profile(version="2025-09-03")), "example")

    def test_reads_token_without_logging_it(self):
        settings = load_settings(str(self.profile()), "example")
        result = type("R", (), {"stdout": json.dumps({"ok": True, "result": {"value": "notion-secret"}}), "returncode": 0})()
        with patch("notion.subprocess.run", return_value=result) as run:
            self.assertEqual(read_token(settings), "notion-secret")
            self.assertNotIn("notion-secret", repr(run.call_args))

    def test_rejects_arbitrary_operation(self):
        client = Client(load_settings(str(self.profile()), "example"), "x")
        with self.assertRaises(NotionError):
            client.request("arbitrary", {}, {})

    def test_requires_path_id(self):
        client = Client(load_settings(str(self.profile()), "example"), "x")
        with self.assertRaises(NotionError):
            client.request("pages.get", {}, {})

    def test_sanitizes_secrets_and_urls(self):
        self.assertEqual(sanitize({"token": "x", "nested": {"access_token": "y"}}), {"token": "[REDACTED]", "nested": {"access_token": "[REDACTED]"}})
        self.assertEqual(sanitize("https://example.test?a=1&token=x"), "[REDACTED_URL]")

    def test_pagination_collects_results(self):
        client = Client(load_settings(str(self.profile()), "example"), "x")
        responses = iter([{"results": [{"id": "1"}], "has_more": True, "next_cursor": "c"}, {"results": [{"id": "2"}], "has_more": False, "next_cursor": None}])
        with patch.object(client, "_send", side_effect=lambda request: next(responses)):
            result = client.request("users.list", {}, {}, paginate=True)
        self.assertEqual(result["results"], [{"id": "1"}, {"id": "2"}])


if __name__ == "__main__":
    unittest.main()

