"""Cobertura do estado que atualiza o MCP sem reiniciar o gateway."""
from __future__ import annotations

import pytest

from onmyoji_daemon.mcp.reconciliation import McpReconciliation, McpReconciliationError, mcp_reconciliation_pending


class Registration:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def reconcile(self) -> str:
        self.calls += 1
        if self.fail:
            raise RuntimeError("failure")
        return "ready"


def test_pending_state_is_consumed_only_after_successful_reconciliation(tmp_path):
    state = McpReconciliation(tmp_path)
    registration = Registration()

    assert mcp_reconciliation_pending(tmp_path) is True
    assert state.reconcile_if_pending(registration) is True
    assert registration.calls == 1 and mcp_reconciliation_pending(tmp_path) is False
    assert state.reconcile_if_pending(registration) is False


def test_failed_reconciliation_keeps_state_pending_for_the_next_gateway_call(tmp_path):
    state = McpReconciliation(tmp_path)

    with pytest.raises(McpReconciliationError):
        state.reconcile_if_pending(Registration(fail=True))

    assert mcp_reconciliation_pending(tmp_path) is True


def test_configuration_change_marks_a_previously_synchronized_state_pending(tmp_path):
    state = McpReconciliation(tmp_path)
    state.mark_synchronized()
    assert mcp_reconciliation_pending(tmp_path) is False

    state.mark_pending()

    assert mcp_reconciliation_pending(tmp_path) is True


def test_windows_transient_replace_error_is_retried_without_losing_state(tmp_path, monkeypatch):
    import os
    state = McpReconciliation(tmp_path); state.mark_synchronized()
    original = os.replace; attempts = []
    def replace(source, target):
        attempts.append(1)
        if len(attempts) < 3:
            error = PermissionError("transient"); error.winerror = 5; raise error
        return original(source, target)
    monkeypatch.setattr("onmyoji_daemon.mcp.reconciliation.os.replace", replace)
    monkeypatch.setattr("onmyoji_daemon.mcp.reconciliation.time.sleep", lambda delay: None)
    state.mark_pending()
    assert len(attempts) == 3 and mcp_reconciliation_pending(tmp_path)


def test_persistent_replace_error_preserves_existing_file_and_removes_temporary(tmp_path, monkeypatch):
    state = McpReconciliation(tmp_path); state.mark_synchronized(); original = state.path.read_bytes()
    def replace(*args):
        error = PermissionError("persistent"); error.winerror = 5; raise error
    monkeypatch.setattr("onmyoji_daemon.mcp.reconciliation.os.replace", replace)
    monkeypatch.setattr("onmyoji_daemon.mcp.reconciliation.time.sleep", lambda delay: None)
    with pytest.raises(McpReconciliationError): state.mark_pending()
    assert state.path.read_bytes() == original
    assert not state.path.with_suffix(".tmp").exists()
