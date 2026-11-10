"""Fronteiras de geração e mutex ao carregar contexto de uma conversa."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from onmyoji_daemon.telegram import CodexProtocolError, Gateway, Settings
from onmyoji_daemon.telegram_delivery import DeliverySuppressed


def gateway(tmp_path: Path) -> Gateway:
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; data.mkdir(parents=True)
    (tmp_path / "configs" / "onmyoji-system.toml").write_text('[codex]\nproject_directory="' + tmp_path.as_posix() + '"\n', encoding="utf-8")
    (tmp_path / "configs" / "daemon" / "daemon.toml").write_text('[app_server]\nenabled=false\n[instructions]\nenabled=true\n', encoding="utf-8")
    (data / "config.toml").write_text('schema_version=1\n[telegram]\nvault_profile="fixture"\ntoken_entry="fixture"\n', encoding="utf-8")
    return Gateway(Settings.load(tmp_path, data))


@pytest.mark.parametrize("mode", ["start", "resume", "missing"])
def test_new_during_thread_rpc_never_reinstates_old_context(tmp_path, mode):
    gw = gateway(tmp_path); gw._ensure_conversation("9")
    if mode != "start":
        gw.database.execute("UPDATE conversations SET codex_thread_id='old-thread',instruction_baseline_hash='old' WHERE chat_id='9'"); gw.database.commit()
    class Client:
        def request(self, method, params):
            assert method == ("thread/start" if mode == "start" else "thread/resume")
            gw._new_conversation("9")
            if mode == "missing": raise CodexProtocolError("thread not found", kind="rejected")
            return {"thread": {"id": "old-thread"}}
    with pytest.raises(DeliverySuppressed):
        gw._app_thread("9", Client())
    assert gw.database.execute("SELECT generation,codex_thread_id,instruction_baseline_hash FROM conversations WHERE chat_id='9'").fetchone() == (1, None, None)


def test_thread_rpc_does_not_hold_database_mutex(tmp_path):
    gw = gateway(tmp_path)
    def probe() -> bool:
        acquired = gw.database_lock.acquire(blocking=False)
        if acquired: gw.database_lock.release()
        return acquired
    class Client:
        def request(self, _method, _params):
            with ThreadPoolExecutor(max_workers=1) as executor:
                assert executor.submit(probe).result(timeout=1)
            return {"thread": {"id": "thread-fixture"}}
    assert gw._app_thread("9", Client()) == "thread-fixture"


def test_group_preparation_from_old_generation_is_not_cached_in_new_context(tmp_path):
    gw = gateway(tmp_path)
    gw._generation("group:-100:1")
    gw._input_local.generation = 0
    gw._new_conversation("group:-100:1")
    gw._cache_message("group:-100:1", {"message_id": 1}, {"id": 9}, "conteúdo antigo", [])
    assert not gw.database.execute("SELECT 1 FROM conversation_messages").fetchone()
