from __future__ import annotations

from onmyoji_daemon.launcher import _approval_response


def test_command_approval_accepts_only_the_active_thread():
    output: list[str] = []
    response = _approval_response(
        "item/commandExecution/requestApproval",
        {"threadId": "thread-1", "command": "git status", "cwd": "C:/work", "availableDecisions": ["accept", "acceptForSession", "decline"]},
        "thread-1",
        read=lambda _prompt: "a",
        write=output.append,
    )
    assert response == {"decision": "accept"}
    assert any("git status" in line for line in output)


def test_command_approval_rejects_another_thread_without_prompting():
    response = _approval_response(
        "item/commandExecution/requestApproval",
        {"threadId": "other", "availableDecisions": ["accept", "decline"]},
        "thread-1",
        read=lambda _prompt: (_ for _ in ()).throw(AssertionError("não deve perguntar")),
    )
    assert response == {"decision": "decline"}


def test_permission_approval_grants_only_the_requested_subset_for_the_session():
    requested = [{"type": "network", "host": "example.com"}]
    response = _approval_response(
        "item/permissions/requestApproval",
        {"threadId": "thread-1", "permissions": requested},
        "thread-1",
        read=lambda _prompt: "s",
    )
    assert response == {"permissions": requested, "scope": "session"}


def test_unknown_or_declined_approval_is_safe_by_default():
    response = _approval_response("unknown", {"threadId": "thread-1"}, "thread-1", read=lambda _prompt: "a")
    assert response == {"decision": "decline"}
