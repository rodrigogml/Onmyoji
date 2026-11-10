"""OAuth remoto limitado às integrações MCP registradas."""
from __future__ import annotations

import json
import sqlite3
import urllib.parse

import pytest

from onmyoji_daemon.mcp.oauth_broker import BTG_CALLBACK_PATH, BTG_INTEGRATION, McpOAuthBroker, OAuthError


class Adapter:
    def __init__(self, root):
        self.root = root
        self.binding = "configuration-v1"
        self.calls = []

    def __call__(self, integration, profile, action, **values):
        self.calls.append((integration, profile, action, values))
        if action == "describe":
            return {"issuer": "https://id.example.com", "authorizationEndpoint": "https://id.example.com/authorize", "redirectUri": "https://apps.example.com" + BTG_CALLBACK_PATH, "storage_root": str(self.root / "configs" / "mcp" / "btg-empresas"), "binding": self.binding, "requireIssuer": True}
        if action == "start":
            return {"authorizationUrl": "https://id.example.com/authorize?" + urllib.parse.urlencode({"state": values["state"], "redirect_uri": values["redirectUri"], "response_type": "code"}), "expiresIn": 600}
        if action == "complete": return {"authenticated": True}
        if action == "cancel": return {"cancelled": True}
        raise AssertionError(action)


@pytest.fixture
def broker(tmp_path):
    root = tmp_path / "code"; root.mkdir()
    service = McpOAuthBroker(root, lambda: "https://apps.example.com")
    adapter = Adapter(root); service.adapter = adapter
    return service, adapter


def callback_query(session):
    values = urllib.parse.parse_qs(urllib.parse.urlsplit(session["authorizationUrl"]).query)
    return {"state": values["state"], "code": ["private-code"], "iss": ["https://id.example.com"]}


def test_btg_is_the_only_oauth_integration_and_uses_mcp_callback(broker):
    service, _ = broker
    session = service.start(BTG_INTEGRATION, "company", True)
    assert session["callbackUrl"] == "https://apps.example.com" + BTG_CALLBACK_PATH
    assert service.callback(BTG_INTEGRATION, callback_query(session)) == session["sessionId"]
    assert service.status(session["sessionId"])["integration"] == BTG_INTEGRATION
    with pytest.raises(OAuthError): service.start("other", "company", True)


def test_oauth_state_is_stored_only_in_configs_and_never_keeps_code(broker):
    service, _ = broker
    session = service.start(BTG_INTEGRATION, "company", True)
    service.callback(BTG_INTEGRATION, callback_query(session))
    with sqlite3.connect(service.path) as con:
        stored = json.dumps(con.execute("SELECT * FROM authorization").fetchall())
    assert service.path.is_relative_to(service.root / "configs")
    assert "private-code" not in stored


def test_callback_is_single_use_and_cancellation_requires_confirmation(broker):
    service, _ = broker
    session = service.start(BTG_INTEGRATION, "company", True)
    with pytest.raises(OAuthError): service.cancel(session["sessionId"], False, BTG_INTEGRATION, "company")
    assert service.cancel(session["sessionId"], True, BTG_INTEGRATION, "company")["status"] == "cancelled"
    with pytest.raises(OAuthError): service.callback(BTG_INTEGRATION, callback_query(session))
