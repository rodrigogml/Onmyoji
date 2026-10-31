"""Contratos e efeitos observáveis da suíte de e-mail."""
from __future__ import annotations

import copy
from contextlib import contextmanager
from email.message import EmailMessage
import json
import smtplib
from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.configuration import McpConfiguration
from onmyoji_daemon.mcp.email.configuration import empty_configuration, validate_configuration
from onmyoji_daemon.mcp.email.contracts import EmailError
from onmyoji_daemon.mcp.email.mailbox import Mailbox
from onmyoji_daemon.mcp.email.messages import authorized_path, compose, content, parse_message, write_file
from onmyoji_daemon.mcp.email.oauth import EmailOAuth
from onmyoji_daemon.mcp.email.service import EmailService
from onmyoji_daemon.mcp.email.store import SubmissionStore
from onmyoji_daemon.mcp.email.tools import EmailToolRegistry
from onmyoji_daemon.mcp.workflows import WorkflowRegistry


def account(protocol="imap", smtp=True):
    auth = {"mode": "password", "credential_profile": "vault", "credential_entry_path": "Email/test", "password_field": "password"}
    value = {"enabled": True, "description": "Correspondência da empresa de teste", "address": "test@example.test", "display_name": "Teste", "allowed_senders": [],
             "readable_roots": [], "writable_roots": [], "sent_copy": "server", "folders": {"inbox": "INBOX", "sent": "Sent", "drafts": "Drafts", "trash": "Trash", "archive": "Archive"}}
    if protocol: value["incoming"] = {"protocol": protocol, "host": "mail.example.test", "port": 993 if protocol == "imap" else 995, "security": "tls", "username": "test@example.test", "auth": copy.deepcopy(auth)}
    if smtp: value["smtp"] = {"protocol": "smtp", "host": "mail.example.test", "port": 465, "security": "tls", "username": "test@example.test", "auth": copy.deepcopy(auth)}
    return value


def configure(root, accounts=None):
    config = McpConfiguration(root)
    credentials = config.credentials()
    credentials["vaults"]["vault"] = {"cli_command": ["keepassxc-cli"], "database": {"windows": "vault.kdbx", "linux": "vault.kdbx"}}
    credentials["profiles"]["vault"] = {"vault": "vault", "allowed_entry_roots": [], "auth": {"windows": {"mode": "windows_credential_manager", "target": "Test/email"}, "linux": {"mode": "command", "command": ["secret-tool"]}}}
    config.save_credentials(credentials)
    document = empty_configuration(); document["accounts"] = accounts if accounts is not None else {"default": account()}
    config.save_email(document)
    return config


class FakeImap:
    def __init__(self):
        self.validity = 42; self.moves = []; self.deleted = []; self.flags = []; self.readonly = []; self.caps = {"MOVE", "UIDPLUS"}; self.appended = []
        msg = EmailMessage(); msg["From"] = "sender@example.test"; msg["To"] = "test@example.test"; msg["Subject"] = "Olá"; msg["Message-ID"] = "<original@example.test>"; msg.set_content("Corpo original")
        msg.add_attachment(b"invoice", maintype="application", subtype="pdf", filename="../fatura.pdf")
        self.raw = msg.as_bytes()
    def select_folder(self, folder, readonly=True): self.readonly.append(readonly); return {b"UIDVALIDITY": self.validity}
    def fetch(self, ids, fields):
        if fields == ["FLAGS"]: return {i: {b"FLAGS": ()} for i in ids}
        if fields == ["RFC822.SIZE"]: return {i: {b"RFC822.SIZE": len(self.raw)} for i in ids}
        return {i: {b"BODY[]": self.raw, b"FLAGS": (), b"RFC822.SIZE": len(self.raw)} for i in ids}
    def search(self, criteria, charset=None): return [1, 2]
    def has_capability(self, value): return value in self.caps
    def move(self, ids, folder): self.moves.append((ids, folder))
    def delete_messages(self, ids, silent=False): self.deleted += ids
    def uid_expunge(self, ids): self.deleted += ids
    def add_flags(self, ids, flags, silent=False): self.flags += flags
    def remove_flags(self, ids, flags, silent=False): pass
    def append(self, folder, raw, flags=()): self.appended.append((folder, raw, flags)); return b"[APPENDUID 42 9] Appended"


class FakeConnections:
    def __init__(self, imap=None, smtp=None): self.imap = imap or FakeImap(); self.smtp = smtp or Mock()
    @contextmanager
    def open(self, kind): yield self.imap if kind == "incoming" else self.smtp


def test_roundtrip_publication_selection_and_workflow(tmp_path):
    config = configure(tmp_path, {})
    assert EmailToolRegistry(config).definitions() == []
    assert "email-operations" not in [item.identifier for item in WorkflowRegistry(config).available()]
    document = config.email(); document["accounts"]["default"] = account(); config.save_email(document)
    tools = EmailToolRegistry(config); definitions = tools.definitions()
    assert "account" not in next(tool for tool in definitions if tool["name"] == "email.get_message")["input_schema"]["properties"]
    assert "Correspondência da empresa" in definitions[0]["description"]
    send = next(tool for tool in definitions if tool["name"] == "email.send_message")
    assert set(send["input_schema"]["properties"]["inline_images"]["items"]["properties"]) == {"path", "content_id"}
    assert WorkflowRegistry(config).call("workflows.read", {"id": "email-operations"})["module"] == "email"
    document["accounts"]["outgoing"] = account(None); config.save_email(document)
    assert len(tools.call("email.list_accounts", {})["accounts"]) == 2
    with pytest.raises(EmailError, match="Parâmetros"): tools.call("email.get_message", {"reference": "bad"})
    read = next(tool for tool in tools.definitions() if tool["name"] == "email.get_message")
    assert read["input_schema"]["properties"]["account"]["enum"] == ["default"]
    assert McpConfiguration(tmp_path).email() == document
    document["accounts"]["default"]["enabled"] = False; config.save_email(document)
    assert "email.list_folders" not in {tool["name"] for tool in tools.definitions()}


@pytest.mark.parametrize("mutate", [lambda a: a["incoming"].update(security="plain"), lambda a: a["smtp"].update(port=0), lambda a: a.update(address="a\r\nBcc: x@y"), lambda a: a.update(description=""), lambda a: a["incoming"].update(protocol="http")])
def test_configuration_rejects_invalid_and_cleartext_connections(mutate):
    document = empty_configuration(); document["accounts"]["default"] = value = account(); mutate(value)
    with pytest.raises(EmailError): validate_configuration(document)


def test_reading_and_pagination_preserve_flags_and_validate_uidvalidity(tmp_path):
    value = account(); client = FakeImap(); connections = FakeConnections(client)
    box = Mailbox("default", value, empty_configuration()["defaults"], connections)
    reference = box.reference("INBOX", 42, 1)
    assert b"Corpo original" in box.raw(reference)
    assert client.readonly == [True]
    assert client.flags == []
    page = box.listing({"limit": 1})
    assert len(page["messages"]) == 1 and page["next_cursor"]
    client.validity = 43
    with pytest.raises(EmailError, match="UIDVALIDITY"): box.raw(reference)
    with pytest.raises(EmailError): box.listing({"cursor": page["next_cursor"], "filters": {"subject": "Outro"}})


def test_selective_deletion_requires_uidplus_before_any_change():
    value = account(); client = FakeImap(); client.caps.clear()
    box = Mailbox("default", value, empty_configuration()["defaults"], FakeConnections(client))
    ref = box.reference("INBOX", 42, 1)
    with pytest.raises(EmailError, match="UIDPLUS"): box.invoke("delete_messages", {"references": [ref]})
    assert client.deleted == []
    with pytest.raises(EmailError, match="Flags"): box.invoke("update_message_flags", {"references": [ref], "add": ["\\Deleted"]})
    assert client.flags == []


def test_mime_safe_paths_and_exclusive_download(tmp_path):
    client = FakeImap(); msg = parse_message(client.raw, 10000)
    assert content(msg, 10)["truncated"] is True
    assert content(msg, 100)["attachments"][0]["filename"] == "../fatura.pdf"
    root = tmp_path.parent / (tmp_path.name + "-work"); root.mkdir()
    with pytest.raises(EmailError): authorized_path(str(root / ".." / "escape"), [str(root)], reading=False)
    target = authorized_path(str(root / "safe.pdf"), [str(root)], reading=False)
    write_file(target, b"safe")
    with pytest.raises(EmailError, match="existe"): authorized_path(str(target), [str(root)], reading=False)
    assert target.read_bytes() == b"safe"


@pytest.mark.parametrize("smtp_result,expected", [({}, "accepted"), ({"refused@example.test": (550, b"private")}, "partially_accepted"), (TimeoutError(), "unknown"), (smtplib.SMTPDataError(550, b"private-secret"), "rejected")])
def test_smtp_deduplicates_and_records_partial_or_unknown_outcomes(tmp_path, monkeypatch, smtp_result, expected):
    configure(tmp_path); smtp = Mock(); smtp.sendmail.side_effect = smtp_result if isinstance(smtp_result, Exception) else None
    smtp.sendmail.return_value = smtp_result
    connections = FakeConnections(smtp=smtp)
    monkeypatch.setattr("onmyoji_daemon.mcp.email.service.Connections", lambda *a: connections)
    service = EmailService(tmp_path); args = {"to": ["ok@example.test", "refused@example.test"], "subject": "Teste", "text": "secret-body", "request_id": "one", "confirm": True}
    result = service.invoke("default", "send_message", args)
    assert result["status"] == expected and result["delivery_confirmed"] is False
    assert service.invoke("default", "send_message", args) == result
    assert smtp.sendmail.call_count == 1
    with pytest.raises(EmailError, match="request_id"): service.invoke("default", "send_message", {**args, "subject": "Alterado"})
    assert service.invoke("default", "get_submission_status", {"request_id": "one"}) == result
    assert b"secret-body" not in (tmp_path / "configs/mcp/email/submissions.sqlite3").read_bytes()
    assert "private-secret" not in str(result)


def test_confirmation_sender_injection_and_extra_arguments_are_rejected(tmp_path):
    config = configure(tmp_path); tools = EmailToolRegistry(config)
    with pytest.raises(EmailError): tools.call("email.send_message", {"to": ["x@example.test"], "request_id": "a"})
    with pytest.raises(EmailError): tools.call("email.get_message", {"reference": "x", "password": "bad"})
    for args in ({"from_address": "other@example.test"}, {"subject": "bad\r\nBcc: hidden@example.test"}, {"to": ["a@example.test\r\nBcc: hidden@example.test"]}):
        with pytest.raises(EmailError): compose(account(), {"to": ["x@example.test"], **args}, empty_configuration()["defaults"])


def test_inline_images_are_related_mime_parts_referenced_by_cid(tmp_path):
    root = tmp_path / "work"; root.mkdir()
    image = root / "logo.png"; image.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    value = account(); value["readable_roots"] = [str(root)]

    message, recipients = compose(value, {"to": ["x@example.test"], "text": "Versão em texto", "html": '<p>Marca <img src="cid:brand-logo"></p>',
                                      "inline_images": [{"path": str(image), "content_id": "brand-logo"}]}, empty_configuration()["defaults"])

    assert recipients == ["x@example.test"]
    assert message.get_content_type() == "multipart/alternative"
    related = message.get_payload()[-1]
    assert related.get_content_type() == "multipart/related"
    html, embedded = related.get_payload()
    assert html.get_content_type() == "text/html" and "cid:brand-logo" in html.get_content()
    assert embedded.get_content_type() == "image/png"
    assert embedded["Content-ID"] == "<brand-logo>"
    assert embedded.get_content_disposition() == "inline"


@pytest.mark.parametrize("args", [
    {"inline_images": [{"path": "placeholder", "content_id": "logo"}]},
    {"html": '<img src="cid:other">', "inline_images": [{"path": "placeholder", "content_id": "logo"}]},
    {"html": '<img src="cid:bad cid">', "inline_images": [{"path": "placeholder", "content_id": "bad cid"}]},
    {"html": '<img src="cid:logo">', "inline_images": [{"path": "placeholder", "content_id": "logo"}, {"path": "placeholder", "content_id": "logo"}]},
])
def test_inline_images_require_html_reference_and_unique_safe_content_ids(tmp_path, args):
    root = tmp_path / "work"; root.mkdir()
    image = root / "placeholder.png"; image.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    value = account(); value["readable_roots"] = [str(root)]
    args = copy.deepcopy(args)
    for image_specification in args.get("inline_images", []): image_specification["path"] = str(image)

    with pytest.raises(EmailError): compose(value, {"to": ["x@example.test"], **args}, empty_configuration()["defaults"])


def test_inline_images_reject_non_images_and_count_toward_message_limit(tmp_path):
    root = tmp_path / "work"; root.mkdir()
    document = root / "document.pdf"; document.write_bytes(b"not an image")
    image = root / "logo.png"; image.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    value = account(); value["readable_roots"] = [str(root)]

    with pytest.raises(EmailError, match="imagem reconhecida"):
        compose(value, {"to": ["x@example.test"], "html": '<img src="cid:file">', "inline_images": [{"path": str(document), "content_id": "file"}]}, empty_configuration()["defaults"])
    limits = {**empty_configuration()["defaults"], "max_message_bytes": 5}
    with pytest.raises(EmailError, match="Imagens inline"):
        compose(value, {"to": ["x@example.test"], "html": '<img src="cid:logo">', "inline_images": [{"path": str(image), "content_id": "logo"}]}, limits)


def test_send_message_delivers_inline_images_as_related_parts(tmp_path, monkeypatch):
    config = configure(tmp_path)
    root = tmp_path.parent / (tmp_path.name + "-work"); root.mkdir()
    (tmp_path / "configs/onmyoji-system.toml").write_text('[codex]\nproject_directory = ' + json.dumps(str(root)), encoding="utf-8")
    image = root / "logo.png"; image.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    document = config.email(); document["accounts"]["default"]["readable_roots"] = [str(root)]; config.save_email(document)
    connections = FakeConnections(); connections.smtp.sendmail.return_value = {}
    monkeypatch.setattr("onmyoji_daemon.mcp.email.service.Connections", lambda *a: connections)
    args = {"to": ["x@example.test"], "text": "Alternativa", "html": '<img src="cid:logo">',
            "inline_images": [{"path": str(image), "content_id": "logo"}], "request_id": "inline-one", "confirm": True}

    result = EmailToolRegistry(config).call("email.send_message", args)

    assert result["status"] == "accepted"
    sent = parse_message(connections.smtp.sendmail.call_args.args[2], 1_000_000)
    related = sent.get_payload()[-1]
    assert related.get_content_type() == "multipart/related"
    assert related.get_payload()[-1]["Content-ID"] == "<logo>"
    assert EmailToolRegistry(config).call("email.send_message", args) == result
    assert connections.smtp.sendmail.call_count == 1


def test_send_draft_retry_survives_original_removal(tmp_path, monkeypatch):
    configure(tmp_path); connections = FakeConnections(); connections.smtp.sendmail.return_value = {}
    connections.imap.raw = connections.imap.raw.replace(b"sender@example.test", b"test@example.test")
    monkeypatch.setattr("onmyoji_daemon.mcp.email.service.Connections", lambda *a: connections)
    box = Mailbox("default", account(), empty_configuration()["defaults"], connections)
    args = {"reference": box.reference("Drafts", 42, 1), "request_id": "draft-one", "confirm": True}
    service = EmailService(tmp_path); result = service.invoke("default", "send_draft", args)
    connections.imap.fetch = Mock(side_effect=RuntimeError("gone"))
    assert service.invoke("default", "send_draft", args) == result
    assert connections.smtp.sendmail.call_count == 1


def test_oauth_pkce_consumption_refresh_rotation_and_public_client(tmp_path, monkeypatch):
    value = account(); value["incoming"]["auth"].update(mode="oauth2", provider="google", client_id="test-client", client_type="public", callback_mode="local", callback_port=8765)
    oauth = EmailOAuth(tmp_path, value, "incoming")
    saved = {}; oauth.read = lambda: copy.deepcopy(saved)
    oauth.save = lambda data: (saved.clear(), saved.update(copy.deepcopy(data)))
    response = Mock(); response.__enter__ = lambda s: s; response.__exit__ = lambda *a: None
    response.read.return_value = b'{"access_token":"access","refresh_token":"refresh"}'
    opener = Mock(); opener.open.return_value = response
    monkeypatch.setattr("onmyoji_daemon.mcp.email.oauth.request.build_opener", lambda *a: opener)
    oauth.credentials = Mock()
    redirect = oauth.metadata()["redirectUri"]
    started = oauth.start("test-state", "session", redirect)
    assert "code_challenge_method=S256" in started["authorizationUrl"]
    assert "code_verifier" not in started["authorizationUrl"]
    with pytest.raises(EmailError): oauth.complete("code", "wrong-state", "session", redirect)
    assert oauth.complete("code", "test-state", "session", redirect)["authenticated"]
    oauth.credentials.resolve.assert_not_called()
    with pytest.raises(EmailError): oauth.complete("code", "test-state", "session", redirect)
    response.read.return_value = b'{"access_token":"next","refresh_token":"rotated"}'
    assert oauth.access_token() == "next"
    assert saved["email_oauth"]["tokens"][oauth.binding]["refresh_token"] == "rotated"


def test_oauth_remote_callback_uses_closed_email_adapter_and_preserves_btg(tmp_path):
    from onmyoji_daemon.mcp.oauth_broker import McpOAuthBroker, EMAIL_CALLBACK_PATH, EMAIL_INTEGRATION
    broker = McpOAuthBroker(tmp_path, lambda: "https://apps.example.test")
    def adapter(integration, profile, action, **args):
        if action == "describe": return {"issuer": "https://accounts.google.com", "authorizationEndpoint": "https://accounts.google.com/authorize", "redirectUri": "https://apps.example.test" + EMAIL_CALLBACK_PATH, "binding": "binding", "storage_root": str(tmp_path / "configs/mcp/email")}
        if action == "start":
            from urllib.parse import urlencode
            return {"authorizationUrl": "https://accounts.google.com/authorize?" + urlencode({"state": args["state"], "redirect_uri": args["redirectUri"], "response_type": "code"}), "expiresIn": 600}
        return {"authenticated": True}
    broker.adapter = adapter
    session = broker.start(EMAIL_INTEGRATION, "default--incoming", True)
    from urllib.parse import parse_qs, urlsplit
    query = parse_qs(urlsplit(session["authorizationUrl"]).query); query["code"] = ["code"]
    broker.callback(EMAIL_INTEGRATION, query)
    assert broker.status(session["sessionId"])["status"] == "completed"
    with pytest.raises(ValueError): broker.callback(EMAIL_INTEGRATION, query)


def test_oauth_microsoft_requests_only_configured_protocol_scopes(tmp_path):
    value = account("pop3"); value["incoming"]["auth"].update(mode="oauth2", provider="microsoft", client_id="client", callback_mode="remote", redirect_uri="https://apps.example.test/oauth/mcp/email/callback", tenant="common")
    metadata = EmailOAuth(tmp_path, value, "incoming").metadata()
    assert metadata["scopes"] == ["offline_access", "https://outlook.office.com/POP.AccessAsUser.All", "https://outlook.office.com/SMTP.Send"]
    assert metadata["tokenEndpoint"] == "https://login.microsoftonline.com/common/oauth2/v2.0/token"


def test_oauth_local_listener_ignores_invalid_state_and_closes_on_success(tmp_path, monkeypatch):
    import socket
    import threading
    from urllib.request import urlopen
    from urllib.error import HTTPError
    from urllib.parse import parse_qs, urlsplit, urlencode
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    value = account(); value["incoming"]["auth"].update(mode="oauth2", provider="google", client_id="client", callback_mode="local", callback_port=port)
    oauth = EmailOAuth(tmp_path, value, "incoming")
    saved = {}; oauth.read = lambda: copy.deepcopy(saved); oauth.save = lambda data: (saved.clear(), saved.update(copy.deepcopy(data)))
    oauth.exchange = lambda fields: {"access_token": "access", "refresh_token": "refresh"}
    threads = []; responses = []
    def open_browser(url):
        query = parse_qs(urlsplit(url).query)
        def browser():
            try: urlopen(query["redirect_uri"][0] + "?state=wrong&code=code", timeout=5)
            except HTTPError as error: responses.append(error.code)
            with urlopen(query["redirect_uri"][0] + "?" + urlencode({"state": query["state"][0], "code": "code"}), timeout=5) as response: responses.append(response.status)
        thread = threading.Thread(target=browser); thread.start(); threads.append(thread)
    monkeypatch.setattr("onmyoji_daemon.mcp.email.oauth.webbrowser.open", open_browser)
    assert oauth.authorize_local(lambda url: None) == {"authenticated": True}
    for thread in threads: thread.join(timeout=5)
    assert responses == [400, 200]
    assert saved["email_oauth"]["pending"] == {}


def test_workspace_roots_block_configs_and_downloaded_files_are_editable(tmp_path, monkeypatch):
    root = tmp_path / "root"; work = tmp_path / "work"; work.mkdir()
    config = configure(root)
    (root / "configs/onmyoji-system.toml").write_text('[codex]\nproject_directory = ' + json.dumps(str(work)), encoding="utf-8")
    document = config.email(); document["accounts"]["default"]["writable_roots"] = [str(root / "configs")]
    with pytest.raises(ValueError): config.save_email(document)
    downloads = work / ".onmyoji" / "email"
    document["accounts"]["default"]["writable_roots"] = [str(downloads)]; config.save_email(document)
    connections = FakeConnections()
    monkeypatch.setattr("onmyoji_daemon.mcp.email.service.Connections", lambda *a: connections)
    box = Mailbox("default", document["accounts"]["default"], document["defaults"], connections)
    target = downloads / "anexo.pdf"
    result = EmailService(root).invoke("default", "download_attachment", {"reference": box.reference("INBOX", 42, 1), "attachment_id": "1", "output_path": str(target), "confirm": True})
    assert result["bytes"] == 7 and target.read_bytes() == b"invoice"
    assert downloads.is_dir()
    target.write_bytes(b"editado")
    assert target.read_bytes() == b"editado"


def test_pop3_requires_uidl_and_never_commits_during_reading():
    value = account("pop3"); client = Mock(); client.uidl.side_effect = RuntimeError("unsupported")
    box = Mailbox("default", value, empty_configuration()["defaults"], FakeConnections(client))
    with pytest.raises(EmailError, match="UIDL"): box.listing({})
    client.dele.assert_not_called(); client.quit.assert_not_called()


def test_submission_reservation_serializes_concurrent_callers(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store = SubmissionStore(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.begin("default", "request", "binding", "digest"), range(2)))
    assert results.count(None) == 1
    assert next(result for result in results if result is not None)["status"] == "unknown"


def test_broker_launches_the_closed_email_adapter_from_an_unrelated_directory(tmp_path, monkeypatch):
    import sys
    from onmyoji_daemon.mcp.oauth_broker import McpOAuthBroker, OAuthError
    monkeypatch.setattr("onmyoji_daemon.mcp.runtime.mcp_runtime_python", lambda root: sys.executable)
    monkeypatch.chdir(tmp_path)
    broker = McpOAuthBroker(tmp_path, lambda: "https://apps.example.test")
    with pytest.raises(OAuthError, match="recusou"):
        # Falha funcional por conta inexistente prova que o adaptador foi importado.
        broker.adapter("email", "missing--incoming", "describe")
