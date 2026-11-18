"""Verifica o reinício completo de um supervisor em processo real, sem serviços externos."""
from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

from onmyoji_daemon import management


def test_local_restart_replaces_the_real_process_and_applies_the_saved_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    processes = []
    launcher = SimpleNamespace(**vars(subprocess))

    def spawn(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        processes.append(process)
        return process

    launcher.Popen = spawn
    monkeypatch.setattr(management, "subprocess", launcher)
    monkeypatch.setattr(management, "ensure_runtime", lambda *_args, **_kwargs: (True, "Runtime do teste"))
    monkeypatch.setattr(management, "daemon_python", lambda _root: sys.executable)
    assert management.prepare_instance(tmp_path)[0]
    try:
        ok, message = management.daemon_action(tmp_path, "start")
        assert ok, message
        original = management._confirmed_daemon(tmp_path)
        assert original is not None
        manifest = management.manifest_path(tmp_path)
        manifest.write_text(manifest.read_text(encoding="utf-8") + "\n# Configuração alterada no teste\n", encoding="utf-8")

        ok, message = management.daemon_action(tmp_path, "restart")

        assert ok, message
        current = management._confirmed_daemon(tmp_path)
        assert current is not None
        assert current["instance"] != original["instance"]
        assert current["pid"] != original["pid"]
        assert current["configuration_revision"] != original["configuration_revision"]
        assert current["configuration_revision"] == management.configuration_revision(tmp_path)
        assert current["setup_version"] == management.SETUP_VERSION
        assert processes[0].wait(timeout=5) == 0
        ok, message = management.daemon_action(tmp_path, "stop")
        assert ok, message
        assert not management.daemon_reachable(tmp_path)
        assert processes[1].wait(timeout=5) == 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
