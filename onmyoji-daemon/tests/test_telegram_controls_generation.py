"""Controles antigos não alteram nem publicam dados após reinício da conversa."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import threading
import time

import pytest

from onmyoji_daemon.telegram import Gateway, Settings, TelegramApi


class ControlApi(TelegramApi):
    def __init__(self):
        self.gateway = None
        self.sent, self.edited, self.deleted = [], [], []
        self.block_send, self.block_edit = False, False
        self.block_text = None
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()

    def _assert_database_is_free(self):
        assert not self.gateway.database_lock._is_owned(), "HTTP não deve ocorrer sob database_lock"
        assert not self.gateway.control_state_lock._is_owned(), "HTTP não deve ocorrer sob control_state_lock"

    def send(self, chat, text, **kwargs):
        self._assert_database_is_free()
        if self.block_send or self.block_text == text:
            self.block_send, self.block_text = False, None
            self.entered.set(); assert self.release.wait(3)
        self.sent.append((chat, text, kwargs))
        return {"message_id": 100 + len(self.sent)}

    def edit(self, chat, message, text, keyboard):
        self._assert_database_is_free()
        if self.block_edit:
            self.entered.set(); assert self.release.wait(3)
        self.edited.append((chat, message, text, keyboard))
        return True

    def delete(self, chat, message):
        self._assert_database_is_free(); self.deleted.append((chat, message))
        return True

    def call(self, *_args, **_kwargs): return True
    def answer_callback(self, *_args): return None
    def typing(self, *_args, **_kwargs):
        self._assert_database_is_free(); return True


@pytest.fixture
def control_gateway(tmp_path: Path, monkeypatch):
    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    data = root / "configs" / "daemon" / "services" / "telegram"; data.mkdir(parents=True)
    workspace.mkdir()
    (root / "configs" / "onmyoji-system.toml").write_text('[codex]\nproject_directory="' + workspace.as_posix() + '"\n', encoding="utf-8")
    (root / "configs" / "daemon" / "daemon.toml").write_text('[app_server]\nenabled=false\n[instructions]\nenabled=true\n', encoding="utf-8")
    (data / "config.toml").write_text('schema_version=2\n[telegram]\nvault_profile="fixture"\ntoken_entry="fixture"\n[totp]\nenabled=false\nreal_password_entry="fixture-real"\nfake_password_entry="fixture-fake"\n', encoding="utf-8")
    settings = replace(Settings.load(root, data), voice_enabled=True, totp_enabled=True, owner_allowed_models=("gpt-a", "gpt-b"), owner_allowed_efforts=("low", "high"))
    gw = Gateway(settings); api = ControlApi(); api.gateway = gw; gw.api = api
    gw.contacts.add_owner({"id": 9})
    monkeypatch.setattr(gw, "_schedule_delete", lambda *_args: None)
    yield gw, api
    api.release.set()


@pytest.mark.parametrize("conversation", ["9", "private:9:42", "direct:9:77"])
def test_old_generation_callback_cannot_change_preferences(control_gateway, conversation):
    gw, api = control_gateway
    gw._open_config(conversation)
    token, session = next(iter(gw.config_sessions.items()))
    assert session["generation"] == 0
    gw._new_conversation(conversation)
    gw.config_sessions[token] = session  # Simula callback cujo handler já reteve a sessão antiga.
    assert gw._config_callback(conversation, {}, ["cfg", token, "share"])
    assert gw._conversation_settings(conversation)["share_thoughts"] is True
    assert token not in gw.config_sessions
    assert len(api.sent) == 1


def test_opening_http_after_reset_does_not_register_old_session(control_gateway):
    gw, api = control_gateway
    api.block_send = True; api.release.clear()
    with ThreadPoolExecutor(max_workers=1) as executor:
        opening = executor.submit(gw._open_config, "9")
        assert api.entered.wait(1)
        gw._new_conversation("9")
        api.release.set(); opening.result(timeout=2)
    assert gw.config_sessions == {}
    assert (9, 101) in api.deleted


def test_model_catalog_reply_after_reset_does_not_render_or_restore_old_menu(control_gateway, monkeypatch):
    gw, api = control_gateway
    gw._open_config("9"); token, session = next(iter(gw.config_sessions.items()))
    entered, release = threading.Event(), threading.Event()
    class Client:
        def request(self, method, params, **kwargs):
            assert method == "model/list"
            api._assert_database_is_free()
            entered.set(); assert release.wait(3)
            return {"data": [{"model": "gpt-a", "supportedReasoningEfforts": [{"reasoningEffort": "low"}]}]}
    monkeypatch.setattr(gw, "_app_client", lambda: Client())
    with ThreadPoolExecutor(max_workers=1) as executor:
        choosing = executor.submit(gw._config_callback, "9", {}, ["cfg", token, "models"])
        assert entered.wait(1)
        gw._new_conversation("9")
        release.set(); assert choosing.result(timeout=2)
    assert token not in gw.config_sessions
    assert len(api.edited) == 1 and "Consultando modelos" in str(api.edited[0][3])
    assert gw._conversation_settings("9")["owner_model"] == ""


@pytest.mark.parametrize("action", ["share", "delete", "model", "effort", "model-1", "effort-1", "execution-reset", "audio"])
def test_reset_between_read_and_mutation_cannot_write_new_generation(control_gateway, monkeypatch, action):
    gw, api = control_gateway
    gw._open_config("9"); token, session = next(iter(gw.config_sessions.items()))
    entered, release = threading.Event(), threading.Event()
    original = gw._conversation_settings
    def settings(conversation):
        value = original(conversation)
        if threading.current_thread().name.startswith("old-config"):
            entered.set(); assert release.wait(3)
        return value
    monkeypatch.setattr(gw, "_conversation_settings", settings)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="old-config") as executor:
        choosing = executor.submit(gw._config_callback, "9", {}, ["cfg", token, action])
        assert entered.wait(1)
        gw._new_conversation("9")
        with gw.database_lock, gw.database:
            gw.database.execute("UPDATE conversations SET share_thoughts=0,delete_thoughts=0,owner_model='survivor',owner_effort='survivor',reply_mode='text' WHERE chat_id='9'")
            expected = gw.database.execute("SELECT generation,share_thoughts,delete_thoughts,owner_model,owner_effort,reply_mode,settings_revision FROM conversations WHERE chat_id='9'").fetchone()
        release.set(); assert choosing.result(timeout=2)
    assert gw.database.execute("SELECT generation,share_thoughts,delete_thoughts,owner_model,owner_effort,reply_mode,settings_revision FROM conversations WHERE chat_id='9'").fetchone() == expected
    assert token not in gw.config_sessions


@pytest.mark.parametrize("conversation", ["9", "direct:9:77"])
def test_render_http_in_flight_is_invalidated_after_new(control_gateway, conversation):
    gw, api = control_gateway
    gw._open_config(conversation); token, session = next(iter(gw.config_sessions.items()))
    api.release.clear()
    if conversation.startswith("direct:"): api.block_send = True
    else: api.block_edit = True
    with ThreadPoolExecutor(max_workers=1) as executor:
        changing = executor.submit(gw._config_callback, conversation, {}, ["cfg", token, "thoughts"])
        assert api.entered.wait(1)
        gw._new_conversation(conversation)
        api.release.set(); assert changing.result(timeout=2)
    assert token not in gw.config_sessions
    assert (9, 101) in api.deleted
    if conversation.startswith("direct:"): assert (9, 102) in api.deleted


@pytest.mark.parametrize("stage", ["password", "entries"])
def test_totp_password_await_cannot_restore_selection_after_new(control_gateway, monkeypatch, stage):
    gw, api = control_gateway
    entered, release = threading.Event(), threading.Event()
    class Vault:
        def read(self, entry, field="password"):
            api._assert_database_is_free()
            if stage == "password" and entry == "fixture-real":
                entered.set(); assert release.wait(3)
            return "fake-password" if entry == "fixture-fake" else "correct-password"
        def list_totp(self):
            api._assert_database_is_free()
            if stage == "entries": entered.set(); assert release.wait(3)
            return ["fixture-entry"]
    monkeypatch.setattr("onmyoji_daemon.telegram.McpVault", lambda _settings: Vault())
    gw._start_totp("9", {}, "")
    with ThreadPoolExecutor(max_workers=1) as executor:
        checking = executor.submit(gw._totp_password, "9", {"message_id": 10, "text": "correct-password"})
        assert entered.wait(1)
        gw._new_conversation("9")
        gw._start_totp("9", {}, "nova-busca")
        current = gw.totp_sessions["9"]
        release.set(); checking.result(timeout=2)
    assert gw.totp_sessions["9"] is current and current["generation"] == 1 and current["phase"] == "password"
    assert all("Escolha a entrada TOTP" not in row[1] for row in api.sent)


@pytest.mark.parametrize("reset", [False, True])
def test_totp_code_await_never_clears_new_challenge_or_publishes_old_code(control_gateway, monkeypatch, reset):
    gw, api = control_gateway
    generation = gw._generation("9")
    session = {"phase": "selection", "generation": generation, "token": "old-token", "entries": ["fixture-entry"], "expires_at": time.time() + 180, "message_ids": [70]}
    gw.totp_sessions["9"] = session
    entered, release = threading.Event(), threading.Event()
    class Vault:
        def read(self, _entry, field="password"):
            assert field == "totp"; api._assert_database_is_free()
            entered.set(); assert release.wait(3); return "654321"
    monkeypatch.setattr("onmyoji_daemon.telegram.McpVault", lambda _settings: Vault())
    callback = {"id": "choose-code", "from": {"id": 9}, "data": "totp:old-token:0", "message": {"chat": {"id": 9, "type": "private"}, "message_id": 70}}
    with ThreadPoolExecutor(max_workers=1) as executor:
        obtaining = executor.submit(gw._callback, callback); assert entered.wait(1)
        if reset: gw._new_conversation("9")
        gw._start_totp("9", {}, "novo-desafio")
        current = gw.totp_sessions["9"]
        release.set(); obtaining.result(timeout=2)
    assert gw.totp_sessions["9"] is current and current["phase"] == "password"
    assert all(row[1] != "654321" for row in api.sent)


def test_totp_code_http_after_new_deletes_only_its_own_message(control_gateway, monkeypatch):
    gw, api = control_gateway
    gw.totp_sessions["9"] = {"phase": "selection", "generation": gw._generation("9"), "token": "old-token", "entries": ["fixture-entry"], "expires_at": time.time() + 180, "message_ids": []}
    monkeypatch.setattr("onmyoji_daemon.telegram.McpVault", lambda _settings: type("Vault", (), {"read": lambda *_args: "654321"})())
    api.block_text = "654321"; api.release.clear()
    callback = {"id": "choose-code", "from": {"id": 9}, "data": "totp:old-token:0", "message": {"chat": {"id": 9, "type": "private"}, "message_id": 70}}
    with ThreadPoolExecutor(max_workers=1) as executor:
        obtaining = executor.submit(gw._callback, callback); assert api.entered.wait(1)
        gw._new_conversation("9"); gw._start_totp("9", {}, "novo")
        current = gw.totp_sessions["9"]
        api.release.set(); obtaining.result(timeout=2)
    code_index = next(index for index, row in enumerate(api.sent, 101) if row[1] == "654321")
    assert (9, code_index) in api.deleted
    assert gw.totp_sessions["9"] is current
    assert all(not row[1].startswith("Expira em") for row in api.sent)


def test_totp_code_http_cannot_survive_replaced_then_closed_challenge(control_gateway, monkeypatch):
    gw, api = control_gateway
    gw.totp_sessions["9"] = {"phase": "selection", "generation": gw._generation("9"), "token": "old-token", "entries": ["fixture-entry"], "expires_at": time.time() + 180, "message_ids": []}
    monkeypatch.setattr("onmyoji_daemon.telegram.McpVault", lambda _settings: type("Vault", (), {"read": lambda *_args: "654321"})())
    api.block_text = "654321"; api.release.clear()
    callback = {"id": "choose-code", "from": {"id": 9}, "data": "totp:old-token:0", "message": {"chat": {"id": 9, "type": "private"}, "message_id": 70}}
    with ThreadPoolExecutor(max_workers=1) as executor:
        obtaining = executor.submit(gw._callback, callback); assert api.entered.wait(1)
        gw._start_totp("9", {}, "novo"); gw._clear_totp_session("9")
        api.release.set(); obtaining.result(timeout=2)
    code_index = next(index for index, row in enumerate(api.sent, 101) if row[1] == "654321")
    assert (9, code_index) in api.deleted and "9" not in gw.totp_sessions
    assert all(not row[1].startswith("Expira em") for row in api.sent)


@pytest.mark.parametrize("password", ["do-not-persist-password-91c9", "/do-not-persist-password-91c9"])
def test_totp_password_is_redacted_from_sqlite_and_never_becomes_model_input(control_gateway, monkeypatch, password):
    gw, api = control_gateway
    entered, release, completed, model_input = threading.Event(), threading.Event(), threading.Event(), threading.Event()
    class Vault:
        def read(self, entry, field="password"):
            if entry == "fixture-real": entered.set(); assert release.wait(3)
            return "fake-password" if entry == "fixture-fake" else password
        def list_totp(self): return ["fixture-entry"]
    monkeypatch.setattr("onmyoji_daemon.telegram.McpVault", lambda _settings: Vault())
    monkeypatch.setattr(gw, "_enqueue_turn", lambda *_args, **_kwargs: model_input.set())
    original = gw.store.ingress_state
    def state(source, value):
        result = original(source, value)
        if value in {"done", "failed", "uncertain"}: completed.set()
        return result
    monkeypatch.setattr(gw.store, "ingress_state", state)
    gw._start_totp("9", {}, "")
    gw._receive_update({"update_id": 1, "message": {"message_id": 10, "chat": {"id": 9, "type": "private"}, "from": {"id": 9}, "text": password}})
    assert entered.wait(1)
    with gw.database_lock:
        assert password not in "\n".join(gw.database.iterdump())
    gw._new_conversation("9"); release.set(); assert completed.wait(2)
    with gw.database_lock:
        assert password not in "\n".join(gw.database.iterdump())
    assert not model_input.is_set() and "9" not in gw.totp_sessions
    assert (9, 10) in api.deleted


@pytest.mark.parametrize("control", ["config", "totp"])
def test_prepared_control_from_old_generation_cannot_open_new_session(control_gateway, control):
    gw, api = control_gateway
    gw._new_conversation("9")
    gw._input_local.generation = 0
    try:
        if control == "config": gw._open_config("9")
        else: gw._start_totp("9", {}, "")
    finally:
        gw._input_local.generation = None
    assert gw.config_sessions == {} and gw.totp_sessions == {} and api.sent == []


def test_password_from_previous_challenge_cannot_consume_new_challenge(control_gateway, monkeypatch):
    gw, api = control_gateway
    gw._start_totp("9", {}, "")
    session = gw.totp_sessions["9"]
    monkeypatch.setattr("onmyoji_daemon.telegram.McpVault", lambda _settings: pytest.fail("senha antiga não deve abrir o vault"))
    gw._totp_password("9", {"message_id": 10, "text": "senha-antiga"}, expected_token="old-challenge")
    assert gw.totp_sessions["9"] is session and not session.get("checking")
    assert (9, 10) in api.deleted


def test_private_prompt_is_persisted_before_http_and_uses_hashed_identity(control_gateway):
    gw, api = control_gateway
    api.block_send = True; api.release.clear()
    with ThreadPoolExecutor(max_workers=1) as executor:
        opening = executor.submit(gw._start_totp, "9", {}, "")
        assert api.entered.wait(1)
        session = gw.totp_sessions["9"]; prompt = gw.store.private_prompt("9")
        assert prompt["generation"] == 0 and prompt["message_id"] is None
        with gw.database_lock: assert session["token"] not in "\n".join(gw.database.iterdump())
        api.release.set(); opening.result(timeout=2)
    assert gw.store.private_prompt("9")["message_id"] == 101


def test_old_prompt_cleanup_cannot_clear_new_challenge_marker(control_gateway):
    gw, _api = control_gateway
    gw._start_totp("9", {}, ""); previous = gw.totp_sessions["9"]
    gw._start_totp("9", {}, "new"); current = gw.totp_sessions["9"]
    prompt = gw.store.private_prompt("9")
    gw._clear_totp_session("9", expected_session=previous)
    assert gw.totp_sessions["9"] is current and gw.store.private_prompt("9") == prompt
    gw._clear_totp_session("9", expected_session=current)
    assert gw.store.private_prompt("9") is None


def test_consumed_password_clears_persisted_prompt_before_selection(control_gateway, monkeypatch):
    gw, _api = control_gateway
    class Vault:
        def read(self, entry, field="password"):
            return "other-password" if entry == "fixture-fake" else "correct-password"
        def list_totp(self): return ["fixture-entry"]
    monkeypatch.setattr("onmyoji_daemon.telegram.McpVault", lambda _settings: Vault())
    gw._start_totp("9", {}, "")
    gw._totp_password("9", {"message_id": 10, "text": "correct-password"})
    assert gw.totp_sessions["9"]["phase"] == "selection"
    assert gw.store.private_prompt("9") is None


def test_identification_prompt_in_flight_cannot_survive_new(control_gateway):
    gw, api = control_gateway
    gw._channel_register("9", "Fixture", 9)
    gw._channel_menu("9", {"id": 9, "type": "private"}, {"id": 9})
    menu = api.sent[-1]; action = menu[2]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    api.block_send = True; api.release.clear()
    callback = {"id": "identify", "data": action, "from": {"id": 9}, "message": {"chat": {"id": 9, "type": "private"}, "message_id": 101}}
    with ThreadPoolExecutor(max_workers=1) as executor:
        identifying = executor.submit(gw._channel_callback, callback)
        assert api.entered.wait(1)
        assert gw.channel_identify_sessions["9"]["generation"] == 0
        gw._new_conversation("9"); api.release.set(); assert identifying.result(timeout=2)
    assert "9" not in gw.channel_identify_sessions and (9, 102) in api.deleted


def test_stale_identification_session_cannot_change_channel_name(control_gateway, monkeypatch):
    gw, _api = control_gateway
    gw._channel_register("9", "Fixture", 9)
    gw.channel_identify_sessions["9"] = {"owner_id": 9, "generation": 0, "expires_at": time.time() + 180}
    gw._new_conversation("9")
    gw.channel_identify_sessions["9"] = {"owner_id": 9, "generation": 0, "expires_at": time.time() + 180}
    monkeypatch.setattr(gw, "_enqueue_turn", lambda *_args, **_kwargs: None)
    gw._update({"message": {"message_id": 10, "chat": {"id": 9, "type": "private"}, "from": {"id": 9}, "text": "old-alias"}})
    assert "9" not in gw.channel_identify_sessions
    assert gw._channels_list()[0]["name"] != "old-alias"


def test_password_received_while_totp_control_prepares_is_never_persisted(control_gateway, monkeypatch):
    gw, _api = control_gateway
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = gw._start_totp
    def start(*args):
        try:
            entered.set(); assert release.wait(3); return original(*args)
        finally:
            finished.set()
    monkeypatch.setattr(gw, "_start_totp", start)
    monkeypatch.setattr(gw, "_enqueue_turn", lambda *_args, **_kwargs: None)
    sender = {"id": 9}; chat = {"id": 9, "type": "private"}
    gw._receive_update({"update_id": 1, "message": {"message_id": 10, "chat": chat, "from": sender, "text": "/totp"}})
    assert entered.wait(1)
    password = "never-persist-while-control-prepares-794b"
    try:
        gw._receive_update({"update_id": 2, "message": {"message_id": 11, "chat": chat, "from": sender, "text": password}})
        with gw.database_lock: assert password not in "\n".join(gw.database.iterdump())
    finally:
        release.set(); assert finished.wait(2)


def test_callback_during_password_challenge_preserves_private_prompt_marker(control_gateway, monkeypatch):
    gw, _api = control_gateway
    completed, callback_completed = threading.Event(), threading.Event()
    callback_original = gw._config_callback
    def callback_handler(*args):
        try: return callback_original(*args)
        finally: callback_completed.set()
    monkeypatch.setattr(gw, "_config_callback", callback_handler)
    original = gw.store.ingress_state
    def state(source, value):
        result = original(source, value)
        if value in {"done", "failed", "uncertain"}: completed.set()
        return result
    monkeypatch.setattr(gw.store, "ingress_state", state)
    gw._start_totp("9", {}, "")
    prompt = gw.store.private_prompt("9")
    callback = {"id": "old-menu", "from": {"id": 9}, "data": "cfg:old:thoughts", "message": {"message_id": 1, "chat": {"id": 9, "type": "private"}, "text": "old prompt"}}
    gw._receive_update({"update_id": 1, "callback_query": callback})
    assert completed.wait(2) and callback_completed.wait(2)
    assert gw.store.private_prompt("9") == prompt


def test_password_after_gateway_restart_is_redacted_and_discarded(control_gateway, monkeypatch):
    previous, _api = control_gateway
    previous._start_totp("9", {}, "")
    gw = Gateway(previous.settings); api = ControlApi(); api.gateway = gw; gw.api = api
    monkeypatch.setattr(gw, "_schedule_delete", lambda *_args: None)
    completed, model_input = threading.Event(), threading.Event()
    monkeypatch.setattr(gw, "_enqueue_turn", lambda *_args, **_kwargs: model_input.set())
    original = gw.store.ingress_state
    def state(source, value):
        result = original(source, value)
        if value in {"done", "failed", "uncertain"}: completed.set()
        return result
    monkeypatch.setattr(gw.store, "ingress_state", state)
    password = "discard-after-restart-ec91"
    gw._receive_update({"update_id": 1, "message": {"message_id": 10, "chat": {"id": 9, "type": "private"}, "from": {"id": 9}, "text": password}})
    assert completed.wait(2)
    with gw.database_lock: assert password not in "\n".join(gw.database.iterdump())
    assert not model_input.is_set() and (9, 10) in api.deleted
    assert gw.store.private_prompt("9") is None


def test_reset_before_private_password_claim_still_deletes_password_message(control_gateway, monkeypatch):
    gw, api = control_gateway
    gw._start_totp("9", {}, "")
    gw._control_limit = threading.BoundedSemaphore(1)
    assert gw._control_limit.acquire(blocking=False)
    deleted = threading.Event(); original = api.delete
    def remove(chat, message):
        result = original(chat, message)
        if message == 10: deleted.set()
        return result
    monkeypatch.setattr(api, "delete", remove)
    try:
        gw._receive_update({"update_id": 1, "message": {"message_id": 10, "chat": {"id": 9, "type": "private"}, "from": {"id": 9}, "text": "queued-private-password-11ad"}})
        assert not deleted.is_set()
        gw._new_conversation("9")
        assert deleted.wait(1)
    finally:
        gw._control_limit.release()


def test_start_totp_uses_current_ingress_intent_and_rejects_replaced_intent(control_gateway):
    gw, api = control_gateway
    generation = gw._generation("9")
    assert gw.store.record_private_prompt("9", generation, "ingress-intent", time.time() + 180)
    gw._input_local.totp_intent_token = "ingress-intent"
    try:
        gw._start_totp("9", {}, "")
        assert gw.totp_sessions["9"]["token"] == "ingress-intent"
        current = gw.totp_sessions["9"]
        assert gw.store.record_private_prompt("9", generation, "new-intent", time.time() + 180)
        gw._start_totp("9", {}, "old")
    finally:
        gw._input_local.totp_intent_token = None
    assert gw.totp_sessions["9"] is current and len(api.sent) == 1
