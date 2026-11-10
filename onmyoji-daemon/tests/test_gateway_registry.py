"""Contratos de reserva e validação do Gateway compartilhado."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from onmyoji_daemon.gateway.exception import GatewayError
from onmyoji_daemon.gateway.repository import ListenerRegistry, validate_prefix, validate_target


@pytest.fixture
def registry(tmp_path):
    value = ListenerRegistry(tmp_path / "listeners.sqlite3")
    yield value
    value.close()


def declaration(prefix="a", target="http://127.0.0.1:8100", ready=True):
    return {"path_prefix": prefix, "target": target, "ready": ready}


@pytest.mark.parametrize("prefix", [None, "", "A", "a/b", "a%2f", "/a", "..", "internal", "á", "a" * 65])
def test_prefix_rejects_noncanonical_or_reserved_segments(prefix):
    with pytest.raises(GatewayError) as failure:
        validate_prefix(prefix)
    assert failure.value.code == "invalid_prefix"


@pytest.mark.parametrize("prefix", ["a", "oauth", "reikon", "service-v1", "s_1", "a" * 64])
def test_prefix_accepts_one_canonical_segment(prefix):
    assert validate_prefix(prefix) == prefix


@pytest.mark.parametrize("target", [None, "", "http://example.com:80", "http://127.0.0.1", "http://127.0.0.1:0", "http://127.0.0.1:70000", "http://user:secret@localhost:80", "http://localhost:80/path", "http://localhost:80?x=y", "http://localhost:80/#fragment", "file:///a", " http://localhost:80", "http://local\nhost:80", "http://localhost:80\t"])
def test_target_rejects_nonlocal_or_ambiguous_origins(target):
    with pytest.raises(GatewayError) as failure:
        validate_target(target)
    assert failure.value.code == "invalid_target"


@pytest.mark.parametrize("target", ["http://127.0.0.1:80", "https://localhost:443/", "http://[::1]:8100"])
def test_target_accepts_explicit_loopback_origin(target):
    assert validate_target(target) == target.rstrip("/")


def test_conflict_rejects_the_whole_declaration(registry):
    registry.sync("first", "g1", [declaration("a")])
    registry.sync("second", "g2", [declaration("stable")])

    with pytest.raises(GatewayError) as failure:
        registry.sync("second", "g3", [declaration("new"), declaration("a")])

    assert failure.value.code == "prefix_conflict"
    assert failure.value.details == {"path_prefix": "a"}
    assert {item["path_prefix"] for item in registry.list()} == {"a", "stable"}
    assert registry.inspect("stable")["generation"] == "g2"


def test_owner_can_update_target_without_releasing_reservation(registry):
    registry.sync("mini-apps", 1, [declaration()])
    updated = registry.sync("mini-apps", 2, [declaration(target="https://localhost:39391")])
    assert len(updated) == 1
    assert updated[0]["owner"] == "mini-apps"
    assert updated[0]["target"] == "https://localhost:39391"
    assert updated[0]["ready"]


@pytest.mark.parametrize("generation,listeners,available", [(None, [], True), (True, [], True), ("", [], True), ("g", None, True), ("g", [{}], True), ("g", [declaration(), declaration()], True), ("g", [declaration(ready="yes")], True), ("g", [], 1)])
def test_invalid_declarations_do_not_create_reservations(registry, generation, listeners, available):
    with pytest.raises(GatewayError):
        registry.sync("mini-apps", generation, listeners, available)
    assert registry.list() == []


def test_disabled_reservation_survives_sync_and_restart(tmp_path):
    path = tmp_path / "listeners.sqlite3"
    original = ListenerRegistry(path)
    original.sync("mini-apps", "old", [declaration()])
    original.change("disable", "a")
    original.close()

    restarted = ListenerRegistry(path)
    try:
        assert restarted.inspect("a")["state"] == "disabled"
        assert restarted.inspect("a")["target"] is None
        restarted.sync("mini-apps", "new", [declaration(target="http://localhost:9000")])
        assert restarted.inspect("a")["state"] == "disabled"
        with pytest.raises(GatewayError) as failure:
            restarted.sync("another", "g", [declaration()])
        assert failure.value.code == "prefix_conflict"
        assert restarted.change("enable", "a")["ready"]
    finally:
        restarted.close()


def test_restart_preserves_reservation_but_never_old_endpoint(tmp_path):
    path = tmp_path / "listeners.sqlite3"
    old = ListenerRegistry(path)
    old.sync("mini-apps", "old", [declaration()])
    old.close()
    restarted = ListenerRegistry(path)
    try:
        item = restarted.inspect("a")
        assert item["state"] == "unavailable"
        assert item["target"] is None and item["generation"] is None
    finally:
        restarted.close()


def test_missing_declaration_keeps_prefix_reserved_but_unavailable(registry):
    registry.sync("mini-apps", "g", [declaration("a"), declaration("oauth")])
    registry.sync("mini-apps", "g", [declaration("a")])
    assert registry.inspect("a")["ready"]
    assert registry.inspect("oauth")["state"] == "unavailable"
    with pytest.raises(GatewayError) as failure:
        registry.sync("other", "g", [declaration("oauth")])
    assert failure.value.code == "prefix_conflict"


def test_remove_is_sticky_across_sync_and_frees_prefix_for_another_owner(registry):
    registry.sync("first", "g1", [declaration()])
    with pytest.raises(GatewayError) as failure:
        registry.change("remove", "a")
    assert failure.value.code == "confirmation_required"
    assert registry.change("remove", "a", "first", True)["removed"]
    registry.sync("first", "g2", [declaration()])
    assert registry.inspect("a", "first")["removed"]
    registry.sync("second", "g3", [declaration()])
    assert registry.inspect("a")["owner"] == "second"
    with pytest.raises(GatewayError) as failure:
        registry.change("restore", "a", "first", True)
    assert failure.value.code == "prefix_conflict"
    assert registry.inspect("a")["owner"] == "second"


def test_removed_reservation_requires_explicit_restore_and_cannot_be_changed_by_other_owner(registry):
    registry.sync("mini-apps", "g", [declaration()])
    with pytest.raises(GatewayError) as failure:
        registry.change("disable", "a", "other")
    assert failure.value.code == "listener_not_found"
    registry.change("remove", "a", "mini-apps", True)
    with pytest.raises(GatewayError) as failure:
        registry.change("enable", "a", "mini-apps")
    assert failure.value.code == "listener_removed"
    with pytest.raises(GatewayError) as failure:
        registry.change("restore", "a", "mini-apps")
    assert failure.value.code == "confirmation_required"
    assert registry.change("restore", "a", "mini-apps", True)["ready"]


def test_old_unavailable_event_does_not_disable_new_generation(registry):
    registry.sync("mini-apps", "old", [declaration()])
    registry.sync("mini-apps", "new", [declaration()])
    registry.unavailable("mini-apps", "old")
    assert registry.inspect("a")["ready"]
    registry.unavailable("mini-apps", "new")
    assert registry.inspect("a")["state"] == "unavailable"


def test_concurrent_claims_have_exactly_one_owner(registry):
    barrier = threading.Barrier(2)
    def claim(owner):
        barrier.wait()
        try:
            registry.sync(owner, owner, [declaration("shared")])
            return owner
        except GatewayError as failure:
            assert failure.code == "prefix_conflict"
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        owners = list(pool.map(claim, ("first", "second")))
    winners = [owner for owner in owners if owner]
    assert len(winners) == 1
    assert registry.inspect("shared")["owner"] == winners[0]


def test_concurrent_database_connections_preserve_one_reservation(tmp_path):
    path = tmp_path / "listeners.sqlite3"
    first, second = ListenerRegistry(path), ListenerRegistry(path)
    barrier = threading.Barrier(2)
    def claim(item):
        registry, owner = item
        barrier.wait()
        try:
            registry.sync(owner, "generation", [declaration("shared")])
            return owner
        except GatewayError as failure:
            assert failure.code == "prefix_conflict"
            return None
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            owners = list(pool.map(claim, ((first, "first"), (second, "second"))))
        winner = [owner for owner in owners if owner]
        assert len(winner) == 1
        assert first.inspect("shared")["owner"] == winner[0]
    finally:
        first.close()
        second.close()
