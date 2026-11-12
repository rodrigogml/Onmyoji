"""Reconciliação do menu privado de comandos sem acessar um bot real."""
from copy import deepcopy
import ast
import inspect
import io
import json
import re
from textwrap import dedent
import time
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from onmyoji_daemon.telegram import Contacts, Gateway, Settings, TelegramApi, TelegramApiError
from onmyoji_daemon.telegram_commands import (
    CONTROL_COMMANDS,
    REGISTERED_COMMANDS,
    command_catalog_revision,
    command_sync_is_current,
    normalize_language_code,
    owner_commands,
    other_private_commands,
)


COMMAND_NAMES = ["new", "config", "channel", "usage", "resetusage", "login", "fila", "direcionar", "cancel", "resolver"]


class MenuService:
    """Modelo remoto por chat/idioma: gravar o default não altera o português."""

    def __init__(self):
        self.catalogs = {}
        self.buttons = {}
        self.calls = []
        self.set_commands_result = True
        self.set_menu_result = True
        self.after_commands_write = None
        self.ignore_menu_write = False

    def call(self, method, values=None, timeout=45):
        values = deepcopy(values or {})
        self.calls.append((method, values, timeout))
        if method in {"getMyCommands", "setMyCommands"}:
            scope = values["scope"]
            assert scope["type"] in {"chat", "all_private_chats"}, "Catálogos não podem ser publicados globalmente."
            target = (scope["chat_id"], values.get("language_code", "")) if scope["type"] == "chat" else ("all_private_chats", values.get("language_code", ""))
            if method == "getMyCommands":
                return deepcopy(self.catalogs.get(target, []))
            if self.set_commands_result is True:
                self.catalogs[target] = deepcopy(values["commands"])
                if self.after_commands_write:
                    self.after_commands_write(self.catalogs[target])
            return self.set_commands_result
        if method == "getChatMenuButton":
            return deepcopy(self.buttons.get(values["chat_id"], {"type": "default"}))
        if method == "setChatMenuButton":
            if self.set_menu_result is True and not self.ignore_menu_write:
                self.buttons[values["chat_id"]] = deepcopy(values["menu_button"])
            return self.set_menu_result
        pytest.fail(f"Método não esperado na sincronização: {method}")


@pytest.fixture
def menu_api(monkeypatch):
    api, remote = TelegramApi("test-token"), MenuService()
    monkeypatch.setattr(api, "call", remote.call)
    return api, remote


def current_summary(totp_enabled=False):
    return {
        "owners": 2, "verified": 2, "failed": 0,
        "pending": False, "in_progress": False,
        "totp_enabled": totp_enabled,
        "expected": [entry["command"] for entry in owner_commands(totp_enabled)],
        "expected_other_private": [entry["command"] for entry in other_private_commands()],
        "other_private_verified": True,
        "catalog_revision": command_catalog_revision(totp_enabled),
    }


def test_menu_catalog_covers_all_owner_commands_and_respects_telegram_contract():
    commands = owner_commands(False)
    assert [entry["command"] for entry in commands] == COMMAND_NAMES
    assert [entry["command"] for entry in owner_commands(True)] == [*COMMAND_NAMES, "totp"]
    assert len(commands) <= 100
    assert all(re.fullmatch(r"[a-z0-9_]{1,32}", entry["command"]) for entry in commands)
    assert all(1 <= len(entry["description"]) <= 256 for entry in commands)
    assert {"/" + entry["command"] for entry in owner_commands(True)} == REGISTERED_COMMANDS - {"/pair"}
    assert {"/usage", "/resetusage", "/login", "/new", "/cancel", "/resolver", "/pair"} <= CONTROL_COMMANDS
    assert not {"/fila", "/direcionar"}.intersection(CONTROL_COMMANDS)
    assert [entry["command"] for entry in other_private_commands()] == ["new", "resolver"]
    assert set(item["command"] for item in other_private_commands()) <= set(COMMAND_NAMES)
    assert "resetusage" not in {item["command"] for item in other_private_commands()}


def test_documented_permission_matrix_covers_all_registered_commands():
    from pathlib import Path

    documentation_path = Path(__file__).resolve().parents[1] / "docs" / "Telegram.md"
    documentation = documentation_path.read_text(encoding="utf-8")
    matrix = documentation.split("## Matriz de acesso aos comandos do Telegram", 1)[1].split("\n## ", 1)[0]
    rows = re.findall(r"^[|] `(/[^`]+)` [|]", matrix, flags=re.MULTILINE)
    normalized = [row.split(" ", 1)[0] for row in rows]
    assert len(normalized) == len(set(normalized))
    assert set(normalized) == REGISTERED_COMMANDS - {"/resetusage"}
    proposal = documentation_path.with_name("Telegram-2026-11-12.proposed.md")
    proposed_matrix = proposal.read_text(encoding="utf-8").split("## Matriz de acesso aos comandos do Telegram", 1)[1].split("\n## ", 1)[0]
    proposed_rows = re.findall(r"^[|] `(/[^`]+)` [|]", proposed_matrix, flags=re.MULTILINE)
    assert {row.split(" ", 1)[0] for row in proposed_rows} == REGISTERED_COMMANDS
    assert "| `/resetusage` | Somente DM" in proposed_matrix
    assert "| `/usage` | Somente DM" in matrix
    assert "| `/usage` | Somente DM (inclusive tópicos privados): consultar os limites e o consumo da conta Codex. | Não permitido;" in matrix
    assert "| `/new` | DM e grupos: reiniciar a conversa correspondente. | Somente DM" in matrix
    assert "| `/resolver` | DM e grupos:" in matrix
    assert "| `/channel` | DM e grupos:" in matrix
    assert "| `/pair <PIN>` | Somente DM, com PIN temporário válido." in matrix


def test_menu_catalog_covers_commands_recognized_by_the_actual_handlers():
    tree = ast.parse(dedent(inspect.getsource(Gateway._update)))
    handled = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not isinstance(node.left, ast.Name) or node.left.id != "command":
            continue
        for comparator in node.comparators:
            literals = comparator.elts if isinstance(comparator, ast.Set) else (comparator,)
            handled.update(value.value for value in literals
                           if isinstance(value, ast.Constant) and isinstance(value.value, str)
                           and value.value.startswith("/"))
    assert handled - {"/pair"} == {"/" + entry["command"] for entry in owner_commands(True)}


def test_returned_catalog_cannot_mutate_the_next_owner_menu_or_its_revision():
    revision = command_catalog_revision(False)
    changed = owner_commands(False)
    changed[0]["description"] = "Alteração de um chamador"
    changed.pop()
    assert [entry["command"] for entry in owner_commands(False)] == COMMAND_NAMES
    assert command_catalog_revision(False) == revision
    assert command_catalog_revision(True) != revision


@pytest.mark.parametrize("value, expected", [
    ("pt", "pt"), ("pt-BR", "pt"), ("EN-us", "en"),
    (None, ""), ("", ""), (True, ""), (123, ""), ("portuguese", ""),
    ("../pt", ""), ("pt\n", ""), ({"language_code": "pt"}, ""),
])
def test_telegram_language_tags_are_normalized_without_accepting_arbitrary_values(value, expected):
    assert normalize_language_code(value) == expected


def test_summary_requires_complete_current_catalog_instead_of_old_verified_count():
    assert command_sync_is_current(current_summary(), False)
    assert not command_sync_is_current({"owners": 2, "verified": 2, "failed": 0}, False)
    assert not command_sync_is_current(current_summary(), True)


@pytest.mark.parametrize("changes", [
    {"catalog_revision": "old"}, {"expected": COMMAND_NAMES[:4]},
    {"other_private_verified": False}, {"expected_other_private": ["new"]},
    {"verified": 1}, {"failed": 1}, {"pending": True}, {"in_progress": True},
    {"owners": True}, {"verified": "2"}, {"pending": 0},
])
def test_summary_rejects_partial_pending_or_malformed_success(changes):
    assert not command_sync_is_current({**current_summary(), **changes}, False)


def test_other_private_menu_publishes_only_new_and_resolver(menu_api):
    api, remote = menu_api
    assert api.set_other_private_commands()
    assert remote.catalogs[("all_private_chats", "")] == other_private_commands()
    assert not set(entry["command"] for entry in other_private_commands()) - {"new", "resolver"}
    assert [call[0] for call in remote.calls] == ["getMyCommands", "setMyCommands", "getMyCommands"]
    assert all(call[1]["scope"] == {"type": "all_private_chats"} for call in remote.calls)


def test_other_private_menu_readback_failure_is_not_confirmed(menu_api):
    api, remote = menu_api
    remote.after_commands_write = lambda entries: entries.pop()
    assert api.set_other_private_commands() is False


def test_other_private_menu_does_not_rewrite_confirmed_scope(menu_api):
    api, remote = menu_api
    remote.catalogs[("all_private_chats", "")] = other_private_commands()
    assert api.set_other_private_commands()
    assert [call[0] for call in remote.calls] == ["getMyCommands"]


def test_missing_owner_menu_publishes_full_catalog_and_enables_only_that_chat(menu_api):
    api, remote = menu_api
    remote.buttons[10] = {"type": "web_app", "text": "Aplicativo", "web_app": {"url": "https://example.test"}}
    untouched = deepcopy(remote.buttons[10])

    assert api.set_owner_commands(9, False)

    assert remote.catalogs[(9, "")] == owner_commands(False)
    assert remote.buttons[9] == {"type": "commands"}
    assert remote.buttons[10] == untouched
    assert all(0 < timeout <= 8 for _method, _values, timeout in remote.calls)
    assert all(values.get("chat_id", 9) == 9 for method, values, _timeout in remote.calls if "MenuButton" in method)


def test_matching_catalog_and_button_do_not_trigger_redundant_writes(menu_api):
    api, remote = menu_api
    remote.catalogs[(9, "")] = owner_commands(False)
    remote.catalogs[(9, "pt")] = owner_commands(False)
    remote.buttons[9] = {"type": "commands"}

    assert api.set_owner_commands(9, False, ("pt", "pt", ""))

    assert not any(method.startswith("set") for method, _values, _timeout in remote.calls)
    queried = [values["language_code"] for method, values, _timeout in remote.calls if method == "getMyCommands"]
    assert queried == ["", "pt"]


@pytest.mark.parametrize("is_ephemeral", [False, True])
def test_explicit_permanent_commands_are_current_but_ephemeral_commands_are_repaired(menu_api, is_ephemeral):
    api, remote = menu_api
    remote.catalogs[(9, "")] = [{**entry, "is_ephemeral": is_ephemeral} for entry in owner_commands(False)]
    remote.buttons[9] = {"type": "commands"}

    assert api.set_owner_commands(9, False)

    rewritten = any(method == "setMyCommands" for method, _values, _timeout in remote.calls)
    assert rewritten is is_ephemeral


def test_old_language_specific_catalog_is_repaired_even_with_current_default(menu_api):
    api, remote = menu_api
    remote.catalogs[(9, "")] = owner_commands(False)
    remote.catalogs[(9, "pt")] = owner_commands(False)[:4]
    remote.buttons[9] = {"type": "commands"}

    assert api.set_owner_commands(9, False, ("pt-BR",))

    assert remote.catalogs[(9, "pt")] == owner_commands(False)
    writes = [values["language_code"] for method, values, _timeout in remote.calls if method == "setMyCommands"]
    assert writes == ["pt"]


@pytest.mark.parametrize("change", ["description", "order"])
def test_same_command_names_with_stale_description_or_order_are_replaced(menu_api, change):
    api, remote = menu_api
    stale = owner_commands(False)
    if change == "description":
        stale[0]["description"] = "Descrição antiga"
    else:
        stale.reverse()
    remote.catalogs[(9, "")] = stale
    remote.buttons[9] = {"type": "commands"}

    assert api.set_owner_commands(9, False)
    assert remote.catalogs[(9, "")] == owner_commands(False)
    assert any(method == "setMyCommands" for method, _values, _timeout in remote.calls)


def test_disabling_totp_removes_it_from_every_managed_language(menu_api):
    api, remote = menu_api
    remote.catalogs[(9, "")] = owner_commands(True)
    remote.catalogs[(9, "pt")] = owner_commands(True)
    remote.buttons[9] = {"type": "commands"}

    assert api.set_owner_commands(9, False, ("pt",))
    assert all([entry["command"] for entry in commands] == COMMAND_NAMES for commands in remote.catalogs.values())


@pytest.mark.parametrize("result", [False, None, 1, "true"])
def test_set_commands_requires_boolean_success(menu_api, result):
    api, remote = menu_api
    remote.set_commands_result = result
    assert api.set_owner_commands(9, False) is False


def test_successful_write_with_different_readback_is_not_marked_verified(menu_api):
    api, remote = menu_api
    remote.after_commands_write = lambda entries: entries.pop()
    assert api.set_owner_commands(9, False) is False


def test_commands_button_is_verified_after_switching_from_web_app(menu_api):
    api, remote = menu_api
    remote.catalogs[(9, "")] = owner_commands(False)
    remote.buttons[9] = {"type": "web_app", "text": "Aplicativo", "web_app": {"url": "https://example.test"}}
    remote.ignore_menu_write = True
    assert api.set_owner_commands(9, False) is False


def test_expired_sync_deadline_starts_no_http_request(menu_api):
    api, remote = menu_api
    with pytest.raises(TelegramApiError):
        api.set_owner_commands(9, False, deadline=time.monotonic() - 1)
    assert remote.calls == []


def test_contacts_preserve_known_languages_across_pairing_and_sparse_updates(tmp_path):
    contacts = Contacts(tmp_path / "contacts.json")
    contacts.add_owner({"id": 9, "language_code": "pt-BR", "first_name": "Owner"})
    assert contacts.owner_languages(9) == ("pt",)
    assert contacts.observe_owner_language({"id": 9, "language_code": "en-US"})
    assert contacts.owner_languages(9) == ("en", "pt")
    assert not contacts.observe_owner_language({"id": 9})
    contacts.add_owner({"id": 9})
    assert contacts.owner_languages(9) == ("en", "pt")
    assert not contacts.observe_owner_language({"id": 9, "language_code": "en"})
    assert Contacts(contacts.path).owner_languages(9) == ("en", "pt")
    assert contacts.owners() == {9}


def test_observing_an_unknown_user_does_not_create_or_promote_an_owner(tmp_path):
    contacts = Contacts(tmp_path / "contacts.json")
    contacts.add_owner({"id": 9})
    assert not contacts.observe_owner_language({"id": 10, "language_code": "pt"})
    assert contacts.owner_languages(10) == ()
    assert contacts.owners() == {9}


@pytest.mark.parametrize("http_error", [True, False])
def test_api_keeps_telegram_retry_after_for_delayed_reconciliation(monkeypatch, http_error):
    payload = {"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 120}}
    encoded = json.dumps(payload).encode()

    def urlopen(*_args, **_kwargs):
        if http_error:
            raise HTTPError("https://api.telegram.org/bottest/getMyCommands", 429, "Too Many Requests", {}, io.BytesIO(encoded))
        return io.BytesIO(encoded)

    monkeypatch.setattr("onmyoji_daemon.telegram.urlopen", urlopen)
    with pytest.raises(TelegramApiError) as caught:
        TelegramApi("test-token").call("getMyCommands", {"scope": {"type": "chat", "chat_id": 9}})
    assert caught.value.retry_after == 120


@pytest.fixture
def menu_gateway(tmp_path):
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "onmyoji-system.toml").write_text(
        '[codex]\nexecutable = "codex"\nproject_directory = "' + tmp_path.as_posix() + '"\n',
        encoding="utf-8",
    )
    data = configs / "daemon" / "services" / "telegram"
    data.mkdir(parents=True)
    (configs / "daemon" / "daemon.toml").write_text('[app_server]\nenabled = false\n', encoding="utf-8")
    (data / "config.toml").write_text(
        'schema_version = 1\n[telegram]\nvault_profile = "telegram"\ntoken_entry = "APIs/Telegram:Example"\n',
        encoding="utf-8",
    )
    gateway = Gateway(Settings.load(tmp_path, data))
    gateway.contacts.add_owner({"id": 9, "language_code": "pt-BR"})
    gateway.contacts.add_owner({"id": 10, "language_code": "en-US"})
    yield gateway
    gateway.stop_event.set()
    gateway.database.close()


def test_gateway_partial_sync_remains_pending_and_recovery_can_verify_all_owners(menu_gateway):
    gateway = menu_gateway
    calls = []

    def configure(owner, totp, languages=(), **_kwargs):
        calls.append((owner, totp, languages))
        return owner == 9

    gateway.api = SimpleNamespace(set_other_private_commands=lambda **_kwargs: True, set_owner_commands=configure)
    summary = gateway._configure_owner_commands()
    assert summary["owners"] == 2 and summary["verified"] == 1 and summary["failed"] == 1
    assert summary["pending"] is True and not command_sync_is_current(summary, False)
    assert summary["expected"] == COMMAND_NAMES
    assert summary["catalog_revision"] == command_catalog_revision(False)
    assert {(owner, tuple(languages)) for owner, _totp, languages in calls} == {(9, ("pt",)), (10, ("en",))}

    gateway.api = SimpleNamespace(set_other_private_commands=lambda **_kwargs: True, set_owner_commands=lambda *_args, **_kwargs: True)
    recovered = gateway._configure_owner_commands()
    assert command_sync_is_current(recovered, False)
    assert recovered["pending"] is False


def test_public_private_menu_failure_keeps_sync_pending_until_verified(menu_gateway):
    gateway = menu_gateway
    public_attempts = []
    gateway.api = SimpleNamespace(
        set_owner_commands=lambda *_args, **_kwargs: True,
        set_other_private_commands=lambda **_kwargs: public_attempts.append(True) and len(public_attempts) > 1,
    )

    first = gateway._configure_owner_commands()
    assert first["owners"] == first["verified"] == 2
    assert first["failed"] == 0 and first["other_private_verified"] is False
    assert first["expected_other_private"] == ["new", "resolver"]
    assert first["pending"] and not command_sync_is_current(first, False)

    recovered = gateway._configure_owner_commands()
    assert recovered["other_private_verified"] is True
    assert recovered["pending"] is False and command_sync_is_current(recovered, False)


def test_gateway_respects_telegram_flood_delay_in_pending_summary(menu_gateway):
    gateway = menu_gateway
    attempts = []

    def rate_limited(owner, *_args, **_kwargs):
        attempts.append(owner)
        raise TelegramApiError("Too Many Requests", kind="not_sent", retry_after=120)

    gateway.api = SimpleNamespace(set_other_private_commands=lambda **_kwargs: True, set_owner_commands=rate_limited)
    before = time.time()
    summary = gateway._configure_owner_commands()
    assert summary["pending"] is True and summary["failed"] > 0
    assert summary["next_retry_at"] >= before + 119
    assert not command_sync_is_current(summary, False)
    assert attempts == [9]
    waiting = gateway._configure_owner_commands()
    assert waiting["pending"] is True and waiting["next_retry_at"] == summary["next_retry_at"]
    assert attempts == [9], "Uma nova chamada manual também precisa respeitar o retry_after."


class ManualClock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def time(self):
        return 1_700_000_000 + self.now


class ScriptedStopEvent:
    """Avança o relógio nos pontos de espera sem criar threads ou dormir."""

    def __init__(self, clock, moments=(), on_wait=None):
        self.clock = clock
        self.moments = iter(moments)
        self.on_wait = on_wait
        self.stopped = False
        self.waits = []

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, timeout):
        self.waits.append(timeout)
        if self.on_wait:
            self.on_wait()
        moment = next(self.moments, None)
        if moment is None:
            self.set()
        else:
            self.clock.now = moment
        return self.stopped


@pytest.fixture
def menu_clock(monkeypatch):
    clock = ManualClock()
    monkeypatch.setattr("onmyoji_daemon.telegram.time", clock)
    return clock


def stored_commands(gateway):
    return json.loads((gateway.settings.state_dir / "gateway-status.json").read_text(encoding="utf-8"))["commands"]


def test_command_worker_retries_failure_after_sixty_seconds_and_clears_pending(menu_gateway, menu_clock):
    gateway, clock = menu_gateway, menu_clock
    attempts, summaries = [], []

    def configure(owner, *_args, **_kwargs):
        attempts.append((clock.now, owner))
        return clock.now >= 1060

    gateway.api = SimpleNamespace(set_other_private_commands=lambda **_kwargs: True, set_owner_commands=configure)
    gateway.stop_event = ScriptedStopEvent(clock, (1059, 1060), lambda: summaries.append(stored_commands(gateway)))

    gateway._owner_commands_watch()

    assert attempts == [(1000, 9), (1000, 10), (1060, 9), (1060, 10)]
    assert summaries[0]["pending"] is True and summaries[0]["failed"] == 2
    assert summaries[0]["next_retry_at"] == 1_700_001_060
    assert command_sync_is_current(summaries[-1], False)
    assert summaries[-1]["next_retry_at"] is None
    assert gateway._commands_next_sync == 1360


def test_command_worker_rechecks_current_menu_after_three_hundred_seconds(menu_gateway, menu_clock):
    gateway, clock = menu_gateway, menu_clock
    attempts = []

    def configure(owner, *_args, **_kwargs):
        attempts.append((clock.now, owner))
        return True

    gateway.api = SimpleNamespace(set_other_private_commands=lambda **_kwargs: True, set_owner_commands=configure)
    gateway.stop_event = ScriptedStopEvent(clock, (1299, 1300))

    gateway._owner_commands_watch()

    assert attempts == [(1000, 9), (1000, 10), (1300, 9), (1300, 10)]
    assert gateway._commands_next_sync == 1600
    assert command_sync_is_current(stored_commands(gateway), False)


def test_new_owner_language_during_sync_remains_pending_until_worker_rechecks(menu_gateway, menu_clock):
    gateway, clock = menu_gateway, menu_clock
    attempts, summaries = [], []

    def configure(owner, _totp, languages, **_kwargs):
        attempts.append((clock.now, owner, languages))
        if owner == 9 and clock.now == 1000:
            gateway._observe_command_language({"id": 9, "language_code": "fr-FR"})
        return True

    gateway.api = SimpleNamespace(set_other_private_commands=lambda **_kwargs: True, set_owner_commands=configure)
    gateway.stop_event = ScriptedStopEvent(clock, (1001,), lambda: summaries.append(stored_commands(gateway)))

    gateway._owner_commands_watch()

    assert [(at, languages) for at, owner, languages in attempts if owner == 9] == [
        (1000, ("pt",)), (1001, ("fr", "pt")),
    ]
    assert summaries[0]["verified"] == 2 and summaries[0]["pending"] is True
    assert not command_sync_is_current(summaries[0], False)
    assert command_sync_is_current(summaries[-1], False)
    assert not gateway._commands_refresh_needed.is_set()


def test_poll_reconnection_requests_menu_sync_before_the_periodic_refresh(menu_gateway, menu_clock):
    gateway, clock = menu_gateway, menu_clock
    attempts, poll_attempts = [], []

    def configure(owner, *_args, **_kwargs):
        attempts.append((clock.now, owner))
        return True

    def call(method, *_args, **_kwargs):
        assert method == "getUpdates"
        poll_attempts.append(clock.now)
        if len(poll_attempts) == 1:
            raise TelegramApiError("Conexão interrompida")
        gateway.stop_event.set()
        return []

    gateway.api = SimpleNamespace(set_other_private_commands=lambda **_kwargs: True, set_owner_commands=configure, call=call)
    assert command_sync_is_current(gateway._configure_owner_commands(), False)
    assert not gateway._commands_refresh_needed.is_set()
    gateway.stop_event = ScriptedStopEvent(clock, (1003,))

    gateway._poll()

    assert poll_attempts == [1000, 1003]
    assert gateway._commands_refresh_needed.is_set()
    assert stored_commands(gateway)["pending"] is True
    assert gateway._commands_next_sync == 1300

    gateway.stop_event = ScriptedStopEvent(clock)
    gateway._owner_commands_watch()

    assert attempts == [(1000, 9), (1000, 10), (1003, 9), (1003, 10)]
    assert command_sync_is_current(stored_commands(gateway), False)


def test_pairing_during_sync_preserves_pending_refresh_for_the_new_owner(menu_gateway, menu_clock):
    gateway, clock = menu_gateway, menu_clock
    attempts, summaries, replies = [], [], []
    gateway.pair = ("123456", clock.time() + 60)
    pairing = {"update_id": 1, "message": {
        "message_id": 17, "chat": {"id": 11, "type": "private"},
        "from": {"id": 11, "language_code": "es-MX"}, "text": "/pair 123456",
    }}

    def configure(owner, _totp, languages, **_kwargs):
        attempts.append((clock.now, owner, languages))
        if owner == 9 and gateway.pair is not None:
            assert gateway._commands_sync_lock.locked()
            gateway._update(pairing)
            assert gateway.contacts.owners() == {9, 10, 11}
            assert gateway._commands_refresh_needed.is_set()
        return True

    gateway.api = SimpleNamespace(
        set_other_private_commands=lambda **_kwargs: True,
        set_owner_commands=configure,
        send=lambda chat, text, **_kwargs: replies.append((chat, text)),
        delete=lambda *_args: None,
    )
    gateway.stop_event = ScriptedStopEvent(clock, (1001,), lambda: summaries.append(stored_commands(gateway)))

    gateway._owner_commands_watch()

    assert [(at, owner) for at, owner, _languages in attempts] == [
        (1000, 9), (1000, 10), (1001, 11),
    ]
    assert attempts[-1][2] == ("es",)
    assert gateway.pair is None
    assert len(replies) == 1 and replies[0][0] == 11 and "pendente" in replies[0][1]
    assert summaries[0]["owners"] == 2 and summaries[0]["pending"] is True
    assert summaries[-1]["owners"] == summaries[-1]["verified"] == 3
    assert command_sync_is_current(summaries[-1], False)
    assert not gateway._commands_refresh_needed.is_set()


def test_partial_command_cycle_reaches_later_owners_and_next_cycle_rechecks_everyone(menu_gateway, menu_clock):
    gateway, clock = menu_gateway, menu_clock
    gateway.contacts.add_owner({"id": 11})
    gateway.contacts.add_owner({"id": 12})
    attempts = []

    def configure(owner, *_args, **_kwargs):
        attempts.append(owner)
        clock.now += 10
        return True

    gateway.api = SimpleNamespace(set_other_private_commands=lambda **_kwargs: True, set_owner_commands=configure)

    partial = gateway._configure_owner_commands()
    assert attempts == [9, 10, 11]
    assert partial["owners"] == 4 and partial["verified"] == 3 and partial["failed"] == 1
    assert partial["pending"] is True and not command_sync_is_current(partial, False)

    clock.now = gateway._commands_next_sync
    complete = gateway._configure_owner_commands()
    assert attempts == [9, 10, 11, 12]
    assert complete["verified"] == 4 and command_sync_is_current(complete, False)

    clock.now = gateway._commands_next_sync
    rechecking = gateway._configure_owner_commands()
    assert attempts == [9, 10, 11, 12, 9, 10, 11]
    assert rechecking["verified"] == 3 and rechecking["pending"] is True

    clock.now = gateway._commands_next_sync
    rechecked = gateway._configure_owner_commands()
    assert attempts == [9, 10, 11, 12, 9, 10, 11, 12]
    assert rechecked["verified"] == 4 and command_sync_is_current(rechecked, False)


def test_owner_consuming_the_entire_budget_does_not_starve_other_owners(menu_gateway, menu_clock):
    gateway, clock = menu_gateway, menu_clock
    gateway.contacts.add_owner({"id": 11})
    gateway.contacts.add_owner({"id": 12})
    attempts = []

    def configure(owner, *_args, **_kwargs):
        attempts.append(owner)
        clock.now += 30 if owner == 9 else 10
        return owner != 9

    gateway.api = SimpleNamespace(set_other_private_commands=lambda **_kwargs: True, set_owner_commands=configure)

    blocked = gateway._configure_owner_commands()
    assert attempts == [9]
    assert blocked["verified"] == 0 and blocked["failed"] == 4 and blocked["pending"] is True

    clock.now = gateway._commands_next_sync
    progressed = gateway._configure_owner_commands()

    assert attempts == [9, 10, 11, 12]
    assert progressed["verified"] == 3 and progressed["failed"] == 1
    assert progressed["pending"] is True and not command_sync_is_current(progressed, False)


@pytest.mark.parametrize("change", ["language", "revision"])
def test_partial_success_is_invalidated_when_its_language_or_catalog_changes(menu_gateway, menu_clock, monkeypatch, change):
    gateway, clock = menu_gateway, menu_clock
    attempts = []
    phase = 1

    def configure(owner, totp, languages, **_kwargs):
        attempts.append((phase, owner, totp, languages))
        return owner == 9 if phase == 1 else owner == 10 if phase == 2 else True

    gateway.api = SimpleNamespace(set_other_private_commands=lambda **_kwargs: True, set_owner_commands=configure)
    original = gateway._configure_owner_commands()
    assert original["verified"] == 1 and original["failed"] == 1

    if change == "language":
        gateway._observe_command_language({"id": 9, "language_code": "fr-FR"})
    else:
        monkeypatch.setattr(gateway, "_totp_enabled", lambda: True)
        gateway._request_command_sync()
    totp = change == "revision"
    expected_languages = ("fr", "pt") if change == "language" else ("pt",)
    phase = 2
    clock.now = gateway._commands_next_sync

    changed = gateway._configure_owner_commands()

    assert (2, 9, totp, expected_languages) in attempts
    assert changed["verified"] == 1 and changed["failed"] == 1
    assert changed["catalog_revision"] == command_catalog_revision(totp)
    assert changed["pending"] is True and not command_sync_is_current(changed, totp)

    phase = 3
    clock.now = gateway._commands_next_sync
    recovered = gateway._configure_owner_commands()

    assert [owner for attempt_phase, owner, _totp, _languages in attempts if attempt_phase == 3] == [9]
    assert command_sync_is_current(recovered, totp)


def test_expired_partial_cycle_rechecks_and_repairs_a_previously_verified_owner(menu_gateway, menu_clock, menu_api):
    gateway, clock = menu_gateway, menu_clock
    api, remote = menu_api
    attempts, repair_states = [], []

    def configure(owner, totp, languages, **kwargs):
        attempts.append(owner)
        if owner == 10 and clock.now < 1600:
            return False
        return api.set_owner_commands(owner, totp, languages, **kwargs)

    gateway.api = SimpleNamespace(set_other_private_commands=lambda **_kwargs: True, set_owner_commands=configure)
    partial = gateway._configure_owner_commands()
    assert partial["verified"] == 1 and partial["failed"] == 1 and partial["pending"] is True
    assert remote.catalogs[(9, "")] == owner_commands(False)

    clock.now += 600
    remote.catalogs[(9, "")] = owner_commands(False)[:4]
    remote.calls.clear()

    def observe_repair(_entries):
        if remote.calls[-1][1]["scope"]["chat_id"] == 9:
            repair_states.append(stored_commands(gateway))

    remote.after_commands_write = observe_repair
    repaired = gateway._configure_owner_commands()

    assert attempts == [9, 10, 9, 10]
    assert remote.catalogs[(9, "")] == owner_commands(False)
    assert repair_states and all(state["pending"] is True and state["in_progress"] is True for state in repair_states)
    default_checks = [method for method, values, _timeout in remote.calls
                      if values.get("scope", {}).get("chat_id") == 9 and values.get("language_code") == ""]
    assert default_checks == ["getMyCommands", "setMyCommands", "getMyCommands"]
    assert repaired["verified"] == 2 and command_sync_is_current(repaired, False)
