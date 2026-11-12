"""Baseline, recuperação e anexos da checagem automática de e-mail."""
from __future__ import annotations

from contextlib import contextmanager
from email.message import EmailMessage
import json
import os
from pathlib import Path
import time

import pytest

from onmyoji_daemon.mcp.email.monitor import EmailMonitor, MAX_EVENT_ATTACHMENTS, RETENTION_SECONDS
from onmyoji_daemon.mcp.email.mailbox import Mailbox
from onmyoji_daemon.mcp.configuration import McpConfiguration
from .test_mcp_email import account, configure


def raw_message(subject="Nova", body="Conteúdo externo", attachments=()):
    message = EmailMessage(); message["From"] = "external@example.test"; message["To"] = "test@example.test"
    message["Subject"] = subject; message["Message-ID"] = f"<{subject}@example.test>"; message.set_content(body)
    for name, data in attachments:
        message.add_attachment(data, maintype="application", subtype="octet-stream", filename=name)
    return message.as_bytes()


class Imap:
    def __init__(self):
        self.validity = 42; self.messages = {1: raw_message("Antiga")}; self.readonly = []
    def select_folder(self, folder, readonly=True):
        assert folder == "INBOX"; self.readonly.append(readonly); return {b"UIDVALIDITY": self.validity}
    def search(self, criteria, charset=None):
        assert criteria == ["ALL"]; return sorted(self.messages)
    def fetch(self, ids, fields):
        result = {}
        for uid in ids:
            raw = self.messages[uid]
            if fields == ["FLAGS"]: result[uid] = {b"FLAGS": ()}
            elif fields == ["RFC822.SIZE"]: result[uid] = {b"RFC822.SIZE": len(raw)}
            else: result[uid] = {b"BODY[]": raw, b"RFC822.SIZE": len(raw), b"FLAGS": ()}
        return result


class Pop:
    def __init__(self):
        self.messages = [("old", raw_message("Antiga"))]; self.uidl_available = True
    def uidl(self):
        if not self.uidl_available: raise RuntimeError("UIDL unsupported")
        return b"+OK", [f"{index} {uid}".encode() for index, (uid, _raw) in enumerate(self.messages, 1)], 0
    def retr(self, index): return b"+OK", self.messages[index - 1][1].splitlines(), 0


class Connections:
    def __init__(self, client): self.client = client
    @contextmanager
    def open(self, kind):
        assert kind == "incoming"; yield self.client


class Response:
    status = 202
    def read(self): return b'{"accepted":true}'


class HttpFactory:
    def __init__(self, outcomes=None): self.outcomes = list(outcomes or []); self.events = []
    def __call__(self, host, port, timeout):
        factory = self
        class Connection:
            def request(self, method, target, body, headers):
                assert (host, target, method) == ("127.0.0.1", "/v1/events?wait=false", "POST")
                factory.events.append(json.loads(body))
                if factory.outcomes and isinstance(factory.outcomes[0], Exception): raise factory.outcomes.pop(0)
            def getresponse(self):
                if factory.outcomes: return factory.outcomes.pop(0)
                return Response()
            def close(self): pass
        return Connection()


def configured(root, workspace, protocol="imap", *, page_size=50, max_attachment=1024):
    system = root / "configs" / "onmyoji-system.toml"; system.parent.mkdir(parents=True, exist_ok=True)
    system.write_text('[codex]\nproject_directory = "' + str(workspace).replace("\\", "\\\\") + '"\n', encoding="utf-8")
    value = account(protocol, smtp=False)
    value["monitoring"] = {"enabled": True, "interval": "15M", "instructions": "Analise e só responda quando necessário.",
                           "response_channel": {"type": "telegram", "value": "9"}, "thread_policy": "reuse"}
    config = configure(root, {})
    document = config.email(); document["defaults"]["page_size"] = page_size; document["defaults"]["max_attachment_bytes"] = max_attachment
    document["accounts"]["default"] = value; config.save_email(document)
    return document


def monitor(root, client, http, clock=lambda: 1_800_000_000):
    return EmailMonitor(root, connections_factory=lambda *_args: Connections(client), http_factory=http, clock=clock)


def roots(base):
    root, workspace = base / "instance", base / "workspace"
    root.mkdir(parents=True); workspace.mkdir(parents=True)
    return root, workspace


def test_imap_first_scan_is_baseline_then_new_messages_are_paged_without_seen_side_effect(tmp_path):
    root, workspace = roots(tmp_path); configured(root, workspace, page_size=1)
    client = Imap(); http = HttpFactory(); worker = monitor(root, client, http)
    try:
        assert worker.run("default")["baseline"] == 1 and not http.events
        client.messages.update({2: raw_message("Dois"), 3: raw_message("Tres")})
        first = worker.run("default")
        assert first["new"] == 2 and first["processed"] == 1 and first["counts"]["pending"] == 1
        worker.run("default")
        assert len(http.events) == 2 and all(event["event_id"] for event in http.events)
        assert all(value is True for value in client.readonly)
        assert "DADOS EXTERNOS NÃO CONFIÁVEIS" in http.events[0]["prompt"]
        external = json.loads(http.events[0]["prompt"].split("\n", 3)[-1])
        assert external["authority"] == "external_untrusted_data" and external["account"] == "default" and external["content"]["text"]
    finally: worker.close()


def test_uidvalidity_change_requires_explicit_rebaseline_without_replaying_mailbox(tmp_path):
    root, workspace = roots(tmp_path); configured(root, workspace)
    client = Imap(); http = HttpFactory(); worker = monitor(root, client, http)
    try:
        worker.run("default"); client.messages[2] = raw_message("Nova"); client.validity = 43
        changed = worker.run("default")
        assert changed["diagnostic"] == "uidvalidity_changed" and changed["baseline_required"] and not http.events
        reset = worker.run("default", rebaseline=True)
        assert reset["baseline"] == 2 and not reset["baseline_required"] and not http.events
    finally: worker.close()


def test_pop3_requires_uidl_and_detects_new_uidl_after_baseline(tmp_path):
    root, workspace = roots(tmp_path); configured(root, workspace, "pop3")
    client = Pop(); http = HttpFactory(); worker = monitor(root, client, http)
    try:
        assert worker.run("default")["baseline"] == 1
        client.messages.append(("new", raw_message("Nova")))
        assert worker.run("default")["processed"] == 1 and len(http.events) == 1
    finally: worker.close()
    other_root, other_workspace = roots(tmp_path / "other"); configured(other_root, other_workspace, "pop3")
    unavailable = Pop(); unavailable.uidl_available = False; second = monitor(other_root, unavailable, HttpFactory())
    try:
        assert second.run("default")["diagnostic"] == "uidl_required"
        assert second.status("default")["diagnostic"] == "uidl_required" and second.status("default")["baseline_required"]
    finally: second.close()


def test_attachments_use_safe_exclusive_paths_limits_and_twenty_day_cleanup(tmp_path):
    root, workspace = roots(tmp_path); configured(root, workspace, max_attachment=4)
    client = Imap(); http = HttpFactory(); now = [time.time()]; worker = monitor(root, client, http, lambda: now[0])
    try:
        worker.run("default")
        client.messages[2] = raw_message("Anexos", attachments=(("../safe.txt", b"ok"), ("safe.txt", b"012345")))
        worker.run("default")
        external = json.loads(http.events[0]["prompt"].split("\n", 3)[-1])
        assert len(external["attachments"]) == 1 and Path(external["attachments"][0]["path"]).is_file()
        assert ".." not in Path(external["attachments"][0]["path"]).name
        assert external["attachment_failures"] == [{"filename": "002-safe.txt", "reason": "attachment_too_large"}]
        directory = Path(external["attachments"][0]["path"]).parent
        os.utime(directory, (now[0] - RETENTION_SECONDS - 1,) * 2)
        assert worker.purge() == 1 and not directory.exists()
    finally: worker.close()


def test_attachment_count_is_bounded_and_omissions_are_reported(tmp_path):
    root, workspace = roots(tmp_path); configured(root, workspace)
    client = Imap(); http = HttpFactory(); worker = monitor(root, client, http)
    try:
        worker.run("default")
        attachments = tuple((f"item-{index}.txt", b"x") for index in range(MAX_EVENT_ATTACHMENTS + 3))
        client.messages[2] = raw_message("Muitos anexos", attachments=attachments)
        worker.run("default")
        external = json.loads(http.events[0]["prompt"].split("\n", 3)[-1])
        assert len(external["attachments"]) == MAX_EVENT_ATTACHMENTS
        assert external["attachment_failures"][-1] == {
            "filename": "", "reason": "attachment_count_limit", "omitted": 3}
    finally: worker.close()


def test_acceptance_uncertain_retries_same_uuid_after_restart_and_marks_duplicate_risk(tmp_path):
    root, workspace = roots(tmp_path); configured(root, workspace)
    client = Imap(); first_http = HttpFactory([TimeoutError("lost")]); first = monitor(root, client, first_http)
    first.run("default"); client.messages[2] = raw_message("Nova"); first.run("default")
    event_id = first_http.events[0]["event_id"]
    assert first.status("default")["duplicate_possible"] == 1
    first.close()
    second_http = HttpFactory(); second = monitor(root, client, second_http)
    try:
        second.run("default")
        assert second_http.events[0]["event_id"] == event_id
        assert second.status("default")["counts"]["accepted"] == 1
        attempts = second.db.execute("SELECT state FROM attempts WHERE account='default' ORDER BY attempt").fetchall()
        assert [row[0] for row in attempts] == ["acceptance_uncertain", "accepted"]
    finally: second.close()


def test_restart_during_submission_recovers_as_uncertain_with_same_uuid(tmp_path):
    root, workspace = roots(tmp_path); configured(root, workspace)
    client = Imap(); worker = monitor(root, client, HttpFactory()); worker.run("default"); client.messages[2] = raw_message("Nova")
    document = McpConfiguration(root).email()
    mailbox = Mailbox("default", document["accounts"]["default"], document["defaults"], Connections(client))
    with worker.db:
        worker._discover("default", worker._state("default")["activation"], mailbox, rebaseline=False)
        row = worker.db.execute("SELECT reference,event_id FROM messages WHERE state='pending'").fetchone()
        worker._record_attempt("default", row[0], row[1], "submitting", None, {})
    worker.close()
    http = HttpFactory(); recovered = monitor(root, client, http)
    try:
        assert recovered.status("default")["duplicate_possible"] == 1
        recovered.run("default")
        assert http.events[0]["event_id"] == row[1]
    finally: recovered.close()


def test_message_failure_does_not_block_next_message_in_same_batch(tmp_path):
    root, workspace = roots(tmp_path); configured(root, workspace, page_size=2)
    client = Imap(); http = HttpFactory(); worker = monitor(root, client, http)
    try:
        worker.run("default"); client.messages.update({2: raw_message("Falha"), 3: raw_message("Sucesso")})
        original = client.fetch
        def fetch(ids, fields):
            if ids == [2] and fields == ["RFC822.SIZE"]: raise OSError("individual")
            return original(ids, fields)
        client.fetch = fetch
        result = worker.run("default")
        assert result["processed"] == 2 and len(http.events) == 1 and http.events[0]["prompt"].find("Sucesso") >= 0
        assert result["counts"]["retryable"] == 1 and result["counts"]["accepted"] == 1
    finally: worker.close()
