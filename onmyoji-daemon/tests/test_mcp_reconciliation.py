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
