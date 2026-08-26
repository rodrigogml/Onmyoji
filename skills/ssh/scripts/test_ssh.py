import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ssh


class FakeChannel:
    def recv_exit_status(self):
        return 0


class FakeStream:
    channel = FakeChannel()

    def read(self):
        return b"ok\n"


class FakeClient:
    def __init__(self):
        self.closed = False

    def exec_command(self, command):
        self.command = command
        return None, FakeStream(), FakeStream()

    def close(self):
        self.closed = True


class SshTests(unittest.TestCase):
    def profile(self, directory, mode="password"):
        return {
            "host": "server", "port": "22", "username": "user", "auth_mode": mode,
            "timeout_seconds": "5", "config_path": str(Path(directory) / "ssh.toml"),
            "vault_profile": "vault", "vault_entry_path": "Servers/server", "keepass_password_field": "password",
            "temp_dir": directory, "known_hosts": "",
            "keepass_key_attachment": "id_ed25519",
        }

    def test_profile_rejects_missing_key_attachment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.ini"
            path.write_text("[ssh]\nhost=server\nusername=user\nauth_mode=key\nkeepass_wrapper=x\nkeepass_config=x\nkeepass_entry=x\n", encoding="utf-8")
            with self.assertRaises(ssh.SshError) as raised:
                ssh.load_profile(str(path), "test")
        self.assertEqual(raised.exception.code, "invalid_profile")

    def test_version_is_required(self):
        with self.assertRaises(ssh.SshError) as raised:
            ssh.handle(self.profile(tempfile.gettempdir()), {"version": 2, "operation": "exec", "command": "true"})
        self.assertEqual(raised.exception.code, "unsupported_version")

    @patch("ssh.connect")
    def test_password_exec_returns_remote_output(self, connect):
        client = FakeClient()
        connect.return_value = client
        result = ssh.handle(self.profile(tempfile.gettempdir()), {"version": 1, "operation": "exec", "command": "echo ok"})
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], "ok\n")
        self.assertTrue(client.closed)

    @patch("ssh.connect")
    @patch("ssh.keepass_request")
    def test_key_file_is_removed_after_session(self, keepass_request, connect):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            connect.return_value = client

            def export(profile, request):
                Path(request["file_path"]).write_text("private", encoding="utf-8")
                return {"exported": True}

            keepass_request.side_effect = export
            ssh.handle(self.profile(directory, "key"), {"version": 1, "operation": "exec", "command": "true"})
            exported = [call.args[1]["file_path"] for call in keepass_request.call_args_list if call.args[1].get("operation") == "attachment.export"]
            self.assertEqual(len(exported), 1)
            self.assertFalse(Path(exported[0]).exists())


if __name__ == "__main__":
    unittest.main()
