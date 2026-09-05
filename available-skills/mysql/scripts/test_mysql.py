import json
import os
import tempfile
import unittest
from unittest.mock import patch

import mysql


def profile():
    text = """[mysql]
executable = mysql
host = localhost
port = 3306
database = test

[auth]
provider_command = keepass-wrapper
entry = APIs/MySQL:test
field = password
username_field = username

[execution]
timeout = 5
allow_client_commands = true
"""
    return {"mysql":{"executable":"mysql","host":"localhost","port":3306,"database":"test"},"auth":{"vault_profile":"test","vault_entry_path":"APIs/MySQL:test","username_field":"username","password_field":"password"},"execution":{"timeout_seconds":5,"allow_client_commands":True}}


class MySQLTests(unittest.TestCase):
    def provider(self, value="secret"):
        return type("Result", (), {"returncode": 0, "stdout": json.dumps({"ok": True, "result": {"value": value}}), "stderr": ""})()

    def mysql_result(self, **kwargs):
        return type("Result", (), {"returncode": 0, "stdout": "id\tname\n1\tAna\n", "stderr": ""})()

    @patch("mysql.subprocess.run")
    def test_query_uses_vault_and_returns_rows(self, run):
        run.side_effect = [self.provider("tester"), self.provider("secret"), self.mysql_result()]
        result = mysql.run(profile(), {"version": 1, "operation": "query", "sql": "SELECT id,name FROM users"})
        self.assertEqual(result["rows"], [{"id": "1", "name": "Ana"}])
        request = json.loads(run.call_args_list[0].kwargs["input"])
        self.assertEqual(request["path"], "APIs/MySQL:test")
        args = run.call_args_list[2]
        self.assertNotIn("secret", args.args[0])
        self.assertEqual(args.kwargs["env"]["MYSQL_PWD"], "secret")

    @patch("mysql.subprocess.run")
    def test_client_can_be_enabled(self, run):
        run.side_effect = [self.provider("tester"), self.provider("secret"), self.mysql_result()]
        result = mysql.run(profile(), {"version": 1, "operation": "client", "args": ["--version"]})
        self.assertIn("id", result["stdout"])

    @patch("mysql.subprocess.run")
    def test_invalid_operation_is_rejected_before_secret_lookup(self, run):
        with self.assertRaises(mysql.MySQLError) as error:
            mysql.run(profile(), {"version": 1, "operation": "arbitrary"})
        self.assertEqual(error.exception.code, "unsupported_operation")
        run.assert_not_called()

    def test_invalid_config(self):
        with tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False, encoding="utf-8") as file:
            file.write("[mysql]\n")
            path = file.name
        try:
            with self.assertRaises(mysql.MySQLError):
                mysql.load_config(path, "example")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
