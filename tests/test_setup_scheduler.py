from unittest.mock import patch

from .test_setup_onmyoji import MODULE


def test_schedule_list_navigates_ten_columns_and_never_mutates_definitions(tmp_path):
    calls = []
    def rpc(root, method, params=None):
        calls.append((method, params))
        return {"state": "running"} if method == "status" else {"items": [], "total": 0}
    choices = ["1", "1", "0", "0", "n", "p", "x"]
    with patch.object(MODULE, "local_daemon_rpc", side_effect=rpc), patch.object(MODULE, "prompt", side_effect=choices), patch.object(MODULE, "screen") as screen:
        MODULE.scheduler_menu(tmp_path)
    lists = [params for method, params in calls if method == "task.list"]
    assert lists[0]["sort"] == "next_run_at" and not lists[0]["descending"]
    assert lists[1]["sort"] == "id" and not lists[1]["descending"]
    assert lists[2]["sort"] == "id" and lists[2]["descending"]
    assert lists[3]["sort"] == "last_result" and not lists[3]["descending"]
    assert lists[4]["sort"] == "last_result" and lists[4]["descending"]
    assert all(method in {"status", "task.list"} for method, params in calls)
    assert all(call.args[0] == "Agendamentos" for call in screen.call_args_list)


def test_event_port_x_cancels_without_write(tmp_path):
    with patch.object(MODULE, "prompt", side_effect=["2", "x", "x"]), patch.object(MODULE, "save_notification_configuration") as save:
        MODULE.notification_menu(tmp_path)
    save.assert_not_called()


def test_event_port_is_configurable(tmp_path):
    with patch.object(MODULE, "prompt", side_effect=["2", "40400", "x"]), patch.object(MODULE, "save_notification_configuration", return_value={"restart_required": True}) as save:
        MODULE.notification_menu(tmp_path)
    save.assert_called_once_with(tmp_path, {"port": 40400})


def test_email_incident_uses_account_and_subject(tmp_path):
    prompts = ["3", "incident@example.com", "Assunto operacional", "x"]
    with patch.object(MODULE, "prompt", side_effect=prompts), patch.object(MODULE, "flow_choice", side_effect=["E-mail", "mail"]), patch.object(MODULE, "McpConfiguration") as configuration, patch.object(MODULE, "save_notification_configuration", return_value={}) as save:
        configuration.return_value.email.return_value = {"accounts": {"mail": {"enabled": True, "smtp": {"host": "test"}}}}
        MODULE.notification_menu(tmp_path)
    save.assert_called_once_with(tmp_path, {"incident": {"type": "email", "value": "incident@example.com", "account": "mail", "subject": "Assunto operacional"}})


def test_scheduler_shows_schedule_timezone_and_exact_columns(tmp_path, capsys):
    entry = {"id": "fixture", "status": "enabled", "action_type": "event", "expression": "0 9 * * *", "timezone": "America/Sao_Paulo", "next_run_at": "2026-11-10T12:00:00.000Z", "created_at": "2026-11-10T10:00:00.000Z", "channel": "telegram", "thread_policy": "reuse", "last_result": None}
    def rpc(root, method, params=None):
        return {"state": "running"} if method == "status" else {"items": [entry], "total": 1}
    with patch.object(MODULE, "local_daemon_rpc", side_effect=rpc), patch.object(MODULE, "prompt", return_value="x"):
        MODULE.scheduler_menu(tmp_path)
    output = capsys.readouterr().out
    assert "2026-11-10 09:00 -0300" in output
    for label in ("ID", "Status", "Ação", "Expressão", "Timezone", "Próxima execução", "Criado", "Canal", "Política de thread", "Último resultado"):
        assert label in output
