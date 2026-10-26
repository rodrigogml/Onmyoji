"""Callback OAuth reutilizável: isolamento, uso único, retomada e navegador remoto."""
from __future__ import annotations
import asyncio
import concurrent.futures
import json
import socket
import sqlite3
import threading
import time
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession, TCPConnector
from onmyoji_daemon.skill_oauth import OAuthError, SkillOAuth, hash_value, oauth_callback_path


class Adapter:
    def __init__(self, root, workspace):
        self.root = root
        self.workspace = workspace
        self.calls = []
        self.binding = "configuration-v1"
        self.fail_complete = False

    def __call__(self, skill, profile, action, **values):
        self.calls.append((skill, profile, action, values))
        redirect = "https://apps.example.com" + oauth_callback_path(skill)
        if action == "describe":
            state = self.root / "configs" / "mcp" / "btg-empresas" if skill == "mcp-btg" else self.workspace
            location = "storage_root" if skill == "mcp-btg" else "workspace"
            return {"issuer": "https://id.example.com", "authorizationEndpoint": "https://id.example.com/authorize", "redirectUri": redirect, location: str(state), "binding": self.binding, "requireIssuer": True}
        if action == "start":
            return {"authorizationUrl": "https://id.example.com/authorize?" + urllib.parse.urlencode({"state": values["state"], "redirect_uri": values["redirectUri"], "response_type": "code"}), "expiresIn": 600}
        if action == "complete":
            if self.fail_complete:
                raise OAuthError("provider unavailable")
            return {"authenticated": True, "access_token": "must-not-escape-adapter"}
        if action == "cancel":
            return {"cancelled": True}
        raise AssertionError(action)


@pytest.fixture
def broker(tmp_path):
    root, workspace = tmp_path / "code", tmp_path / "work"
    root.mkdir(); workspace.mkdir()
    broker = SkillOAuth(root, workspace, lambda: "https://apps.example.com")
    adapter = Adapter(root, workspace)
    broker.adapter = adapter
    return broker, adapter


def query(start, **extra):
    data = urllib.parse.parse_qs(urllib.parse.urlsplit(start["authorizationUrl"]).query)
    return {"state": data["state"], "code": ["private-code"], "iss": ["https://id.example.com"], **extra}


def test_stable_route_temporary_sessions_and_secret_free_storage(broker):
    service, adapter = broker
    first = service.start("first-skill", "company", True)
    assert first["callbackUrl"] == "https://apps.example.com/oauth/skills/first-skill/callback"
    assert service.callback("first-skill", query(first)) == first["sessionId"]
    assert service.status(first["sessionId"])["status"] == "completed"
    assert "concluída" in service.result(first["sessionId"])
    with pytest.raises(OAuthError):
        service.callback("first-skill", query(first))
    second = service.start("first-skill", "company", True)
    assert first["callbackUrl"] == second["callbackUrl"] and first["sessionId"] != second["sessionId"]
    with sqlite3.connect(service.path) as con:
        stored = json.dumps(con.execute("SELECT * FROM authorization").fetchall())
    assert "private-code" not in stored and "access_token" not in stored
    assert query(first)["state"][0] not in stored


def test_oauth_sessions_use_configs_and_copy_legacy_database(tmp_path):
    root, workspace = tmp_path / "code", tmp_path / "work"
    root.mkdir(); workspace.mkdir()
    source = workspace / ".onmyoji" / "skill-oauth" / "sessions.sqlite3"
    source.parent.mkdir(parents=True)
    with sqlite3.connect(source) as con:
        con.execute("CREATE TABLE legacy_marker (value TEXT)")
        con.execute("INSERT INTO legacy_marker VALUES ('preserved')")
    service = SkillOAuth(root, workspace, lambda: "https://apps.example.com")
    assert service.path == root / "configs" / "daemon" / "services" / "mini-apps" / "oauth" / "sessions.sqlite3"
    assert source.is_file()
    with sqlite3.connect(service.path) as con:
        assert con.execute("SELECT value FROM legacy_marker").fetchone()[0] == "preserved"
    without_workspace = SkillOAuth(tmp_path / "no-workspace", None, lambda: "https://apps.example.com")
    assert without_workspace.path.is_relative_to(tmp_path / "no-workspace" / "configs")


def test_parallel_provider_profiles_are_isolated(broker):
    service, adapter = broker
    one = service.start("one", "company", True)
    two = service.start("two", "company", True)
    three = service.start("one", "other", True)
    with pytest.raises(OAuthError):
        service.callback("two", query(one))
    with pytest.raises(OAuthError):
        service.start("one", "company", True)
    with pytest.raises(OAuthError):
        service.status(one["sessionId"], "one", "other")
    assert service.status(two["sessionId"])["status"] == "awaiting"
    assert service.status(three["sessionId"])["status"] == "awaiting"


@pytest.mark.parametrize("change", [{"state": []}, {"state": ["wrong-state-long-enough"]}, {"state": ["a", "b"]}, {"code": []}, {"code": ["one", "two"]}, {"code": [""]}, {"iss": []}, {"iss": ["https://attacker.example"]}, {"error": ["access_denied"]}])
def test_invalid_callbacks_never_exchange_code(broker, change):
    service, adapter = broker
    session = service.start("one", "company", True)
    with pytest.raises(OAuthError):
        service.callback("one", query(session, **change))
    assert not any(action == "complete" for _, _, action, _ in adapter.calls)


def test_provider_denial_and_cancellation_invalidate_session(broker):
    service, adapter = broker
    session = service.start("one", "company", True)
    service.callback("one", query(session, code=[], error=["access_denied"]))
    assert service.status(session["sessionId"])["status"] == "denied"
    session = service.start("one", "company", True)
    with pytest.raises(OAuthError):
        service.cancel(session["sessionId"], False)
    assert service.cancel(session["sessionId"], True)["status"] == "cancelled"
    with pytest.raises(OAuthError):
        service.callback("one", query(session))


def test_expiration_restart_and_interrupted_exchange_never_replay(broker):
    service, adapter = broker
    session = service.start("one", "company", True)
    with service.connection() as con:
        con.execute("UPDATE authorization SET expiresAt=0 WHERE id=?", (session["sessionId"],))
    restarted = SkillOAuth(service.root, service.workspace, service.public_origin)
    restarted.adapter = adapter
    assert restarted.status(session["sessionId"])["status"] == "expired"
    with pytest.raises(OAuthError):
        restarted.callback("one", query(session))
    next_session = restarted.start("one", "company", True)
    with service.connection() as con:
        con.execute("UPDATE authorization SET status='processing',processingAt=0 WHERE id=?", (next_session["sessionId"],))
    assert restarted.status(next_session["sessionId"])["status"] == "interrupted"
    with pytest.raises(OAuthError):
        restarted.callback("one", query(next_session))


def test_configuration_change_and_exchange_failure_are_sanitized(broker):
    service, adapter = broker
    session = service.start("one", "company", True)
    adapter.binding = "changed"
    service.callback("one", query(session))
    assert service.status(session["sessionId"])["errorCode"] == "completion_failed"
    assert not any(action == "complete" for _, _, action, _ in adapter.calls)
    session = service.start("one", "company", True)
    adapter.fail_complete = True
    service.callback("one", query(session))
    assert service.status(session["sessionId"])["status"] == "failed"


def test_callback_concurrency_permits_only_one_exchange(broker):
    service, adapter = broker
    session = service.start("one", "company", True)
    def complete():
        try:
            return service.callback("one", query(session))
        except OAuthError:
            return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: complete(), range(2)))
    assert sum(value is not None for value in values) == 1
    assert sum(action == "complete" for _, _, action, _ in adapter.calls) == 1


def test_adapter_is_fixed_local_script_and_secrets_use_stdin(tmp_path, monkeypatch):
    root, workspace = tmp_path / "code", tmp_path / "work"
    root.mkdir(); workspace.mkdir()
    service = SkillOAuth(root, workspace, lambda: "https://apps.example.com")
    script = root / "available-skills" / "test" / "scripts" / "oauth_adapter.py"
    script.parent.mkdir(parents=True); script.write_text("", encoding="utf-8")
    calls = []
    def run(command, **kwargs):
        calls.append((command, json.loads(kwargs["input"])))
        return SimpleNamespace(returncode=0, stdout='{"ok":true,"data":{"authenticated":true}}')
    monkeypatch.setattr("onmyoji_daemon.skill_oauth.subprocess.run", run)
    assert service.adapter("test", "company", "complete", code="private-code")["authenticated"]
    assert "private-code" not in " ".join(calls[0][0])
    assert calls[0][1]["code"] == "private-code"
    for skill in ["../test", "not-installed"]:
        with pytest.raises(OAuthError):
            service.adapter(skill, "company", "complete")


def test_redirect_configuration_and_confirmation_required(broker):
    service, adapter = broker
    with pytest.raises(OAuthError):
        service.start("one", "company", False)
    service.public_origin = lambda: "https://different.example.com"
    assert service.info("one", "company")["redirectMatches"] is False
    with pytest.raises(OAuthError):
        service.start("one", "company", True)


@pytest.mark.parametrize("integration,callback_path", [("one", "/oauth/skills/one/callback"), ("mcp-btg", "/oauth/mcp/btg/callback")])
def test_https_callback_without_cookie_or_custom_header_and_clean_result(tmp_path, integration, callback_path):
    from onmyoji_daemon.miniapps import MiniAppsGateway
    root, workspace = tmp_path / "code", tmp_path / "work"
    (root / "configs").mkdir(parents=True); workspace.mkdir()
    (root / "configs" / "onmyoji-system.toml").write_text('[codex]\nproject_directory=' + json.dumps(str(workspace)) + '\n', encoding="utf-8")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
    gateway = MiniAppsGateway(root, root / "configs" / "daemon" / "services" / "mini-apps", "test-token", port)
    gateway.tunnel.status = lambda: {"provisioned": True, "running": True, "hostname": "apps.example.com"}
    service = gateway._oauth_broker(); adapter = Adapter(root, workspace); service.adapter = adapter
    gateway.start_http()
    try:
        session = gateway.rpc("mini-apps.oauth.start", {"skill": integration, "profile": "company", "confirm": True})
        async def verify():
            async with ClientSession(connector=TCPConnector(ssl=False)) as browser:
                url = f"https://localhost:{port}" + callback_path + "?" + urllib.parse.urlencode({k: v[0] for k, v in query(session).items()})
                if integration == "mcp-btg":
                    alias = f"https://localhost:{port}/oauth/skills/mcp-btg/callback?" + urllib.parse.urlencode({k: v[0] for k, v in query(session).items()})
                    async with browser.get(alias, allow_redirects=False) as response:
                        assert response.status == 400
                async with browser.get(url, allow_redirects=False) as response:
                    assert response.status == 303
                    location = response.headers["Location"]
                    assert "code=" not in location and "state=" not in location
                    assert response.headers["Referrer-Policy"] == "no-referrer"
                async with browser.get(f"https://localhost:{port}" + location) as response:
                    assert response.status == 200
                    html = await response.text()
                    assert "concluída" in html and "private-code" not in html and "access_token" not in html
                    assert response.headers["Cache-Control"] == "no-store"
                async with browser.get(url, allow_redirects=False) as response:
                    assert response.status == 400
        asyncio.run(verify())
        assert gateway.rpc("mini-apps.oauth.status", {"session_id": session["sessionId"]})["status"] == "completed"
    finally:
        gateway.stop_http()


def test_mcp_btg_adapter_is_fixed_module_with_mcp_runtime_and_secret_in_stdin(tmp_path, monkeypatch):
    root, workspace = tmp_path / "code", tmp_path / "work"
    root.mkdir(); workspace.mkdir()
    service = SkillOAuth(root, workspace, lambda: "https://apps.example.com")
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"ok":true,"data":{"authenticated":true}}')
    monkeypatch.setattr("onmyoji_daemon.skill_oauth.subprocess.run", run)
    assert service.adapter("mcp-btg", "company", "complete", code="private-code")["authenticated"]
    command, kwargs = calls[0]
    assert command[1:3] == ["-m", "onmyoji_daemon.mcp.btg.oauth_adapter"]
    assert "configs" in command[0] and "mcp" in command[0]
    assert "private-code" not in " ".join(command)
    assert json.loads(kwargs["input"])["code"] == "private-code"


def test_gateway_requires_running_tunnel_and_cli_routes_oauth(tmp_path, monkeypatch, capsys):
    from onmyoji_daemon import cli
    from onmyoji_daemon.miniapps import MiniAppsGateway
    root, workspace = tmp_path / "code", tmp_path / "work"
    (root / "configs").mkdir(parents=True); workspace.mkdir()
    (root / "configs" / "onmyoji-system.toml").write_text('[codex]\nproject_directory=' + json.dumps(str(workspace)) + '\n', encoding="utf-8")
    gateway = MiniAppsGateway(root, root / "configs" / "daemon" / "services" / "mini-apps", "test-token", 1234)
    with pytest.raises(OAuthError):
        gateway._oauth_origin()
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "test-token"))
    calls = []
    monkeypatch.setattr(cli, "call", lambda h,p,t,method,params,**kw: calls.append((method, params)) or {"status": "awaiting"})
    assert cli.main(["--onmyoji-root", str(root), "mini-apps", "oauth", "start", "--skill", "mcp-btg", "--profile", "company", "--confirm"]) == 0
    assert calls[0] == ("mini-apps.oauth.start", {"skill": "mcp-btg", "profile": "company", "confirm": True})


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_invalid_authorization_url_fails_and_cleans_up_without_exposure(broker, cleanup_fails):
    service, adapter = broker
    def wrapped(skill, profile, action, **values):
        if action == "start":
            return {"authorizationUrl": "https://attacker.example/?client_secret=private", "expiresIn": 600}
        if action == "cancel" and cleanup_fails:
            raise OAuthError("cleanup unavailable")
        return adapter(skill, profile, action, **values)
    service.adapter = wrapped
    with pytest.raises(OAuthError):
        service.start("one", "company", True)
    with service.connection() as con:
        row = dict(con.execute("SELECT * FROM authorization").fetchone())
    assert row["status"] == "failed" and row["errorCode"] == "start_failed"
    assert "private" not in json.dumps(row)


def test_completion_cleanup_failure_and_cancel_cleanup_failure_are_tracked(broker):
    service, adapter = broker
    def wrapped(skill, profile, action, **values):
        if action in {"complete", "cancel"}:
            raise OAuthError("vault unavailable")
        return adapter(skill, profile, action, **values)
    service.adapter = wrapped
    session = service.start("one", "company", True)
    service.callback("one", query(session))
    assert service.status(session["sessionId"])["status"] == "failed"
    session = service.start("one", "company", True)
    with pytest.raises(OAuthError):
        service.cancel(session["sessionId"], True)
    assert service.status(session["sessionId"])["status"] == "cancelled"
    assert service.status(session["sessionId"])["errorCode"] == "cancellation_cleanup_failed"


def test_adapter_subprocess_failure_is_sanitized(tmp_path, monkeypatch):
    import subprocess
    root, workspace = tmp_path / "code", tmp_path / "work"
    root.mkdir(); workspace.mkdir()
    service = SkillOAuth(root, workspace, lambda: "https://apps.example.com")
    script = root / "available-skills" / "test" / "scripts" / "oauth_adapter.py"
    script.parent.mkdir(parents=True); script.write_text("", encoding="utf-8")
    monkeypatch.setattr("onmyoji_daemon.skill_oauth.subprocess.run", lambda *a,**kw: (_ for _ in ()).throw(subprocess.TimeoutExpired("private-command", 180)))
    with pytest.raises(OAuthError) as caught:
        service.adapter("test", "company", "start")
    assert "private" not in str(caught.value)


def test_second_provider_can_use_different_endpoint_without_issuer_parameter(broker):
    service, adapter = broker
    def other_provider(skill, profile, action, **values):
        result = adapter(skill, profile, action, **values)
        if skill == "legacy-provider" and action == "describe":
            result.update(issuer="https://login.other.example", authorizationEndpoint="https://login.other.example/authorize", requireIssuer=False, binding=hash_value("other-provider-v1"))
        if skill == "legacy-provider" and action == "start":
            result["authorizationUrl"] = result["authorizationUrl"].replace("https://id.example.com", "https://login.other.example")
        return result
    service.adapter = other_provider
    first = service.start("one", "company", True)
    second = service.start("legacy-provider", "company", True)
    data = query(second); data.pop("iss")
    service.callback("legacy-provider", data)
    assert service.status(second["sessionId"])["status"] == "completed"
    assert service.status(first["sessionId"])["status"] == "awaiting"


def test_http_callback_rate_limit_and_result_not_found(tmp_path):
    from onmyoji_daemon.miniapps import MiniAppsGateway
    from aiohttp.test_utils import make_mocked_request
    gateway = MiniAppsGateway(tmp_path, tmp_path / "data", "test-token", 1234)
    request = make_mocked_request("GET", "/oauth/skills/one/callback", match_info={"skill": "one"})
    gateway.oauth_callback_hits[request.remote or "unknown"] = [time.time()] * 30
    assert asyncio.run(gateway.skill_oauth_callback(request)).status == 429
    request = make_mocked_request("GET", "/oauth/result/missing", match_info={"session_id": "missing"})
    assert asyncio.run(gateway.skill_oauth_result(request)).status == 404


def test_provider_error_without_issuer_ends_session_without_exchange(broker):
    service, adapter = broker
    session = service.start("one", "company", True)
    data = query(session); data.pop("iss"); data.pop("code"); data["error"] = ["server_error"]
    service.callback("one", data)
    assert service.status(session["sessionId"])["status"] == "failed"
    assert service.status(session["sessionId"])["errorCode"] == "provider_error"
    assert not any(action == "complete" for _, _, action, _ in adapter.calls)
