from unittest.mock import patch

from .test_setup_onmyoji import MODULE


def test_scheduler_list_navigates_sorting_and_never_mutates_definitions(tmp_path):
    calls = []
    def rpc(root, method, params=None):
        calls.append((method, params))
        return {"state": "running"} if method == "status" else {"items": [], "total": 0}
    with patch.object(MODULE, "local_daemon_rpc", side_effect=rpc), patch.object(MODULE, "prompt", side_effect=["1", "1", "n", "p", "r", "x"]), patch.object(MODULE, "screen") as screen:
        MODULE.scheduler_menu(tmp_path)
    lists = [params for method, params in calls if method == "task.list"]
    assert lists[0]["sort"] == "next_run_at" and not lists[0]["descending"]
    assert lists[1]["sort"] == "name" and not lists[1]["descending"]
    assert lists[2]["sort"] == "name" and lists[2]["descending"]
    assert all(method in {"status", "task.list"} for method, params in calls)
    assert all(call.args[0] == "Agendador" for call in screen.call_args_list)


def test_notification_receiver_choice_is_flow_and_x_cancels_without_write(tmp_path):
    with patch.object(MODULE, "prompt", side_effect=["3", "x"]), patch.object(MODULE, "flow_choice", return_value=None) as choice, patch.object(MODULE, "save_notification_configuration") as save:
        MODULE.notification_menu(tmp_path)
    choice.assert_called_once()
    save.assert_not_called()


def test_notification_smtp_choice_uses_existing_accounts(tmp_path):
    with patch.object(MODULE, "prompt", side_effect=["4", "x"]), patch.object(MODULE, "flow_choice", return_value="mail") as choice, patch.object(MODULE, "McpConfiguration") as configuration, patch.object(MODULE, "save_notification_configuration", return_value={}) as save:
        configuration.return_value.email.return_value = {"accounts": {"mail": {"enabled": True, "smtp": {"host": "test"}}, "off": {"enabled": False, "smtp": {}}}}
        MODULE.notification_menu(tmp_path)
    assert choice.call_args.args[1] == ["Desabilitar e-mail", "mail"]
    save.assert_called_once_with(tmp_path, {"smtp_account": "mail"})
