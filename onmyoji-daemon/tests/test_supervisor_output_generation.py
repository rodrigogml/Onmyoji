"""Saída de uma geração anterior não define o diagnóstico da geração atual."""
from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from onmyoji_daemon import supervisor as supervisor_module
from onmyoji_daemon.supervisor import Supervisor


@pytest.mark.parametrize("replace_process, replace_generation", [(True, False), (False, True), (True, True)])
def test_old_output_is_logged_without_overwriting_current_generation(tmp_path, replace_process, replace_generation):
    supervisor = Supervisor(tmp_path)
    try:
        process = SimpleNamespace(stdout=io.StringIO("old failure token=secret\n"), poll=lambda: 1, returncode=1)
        service = supervisor.services["telegram"]
        service.process = object() if replace_process else process
        service.generation = "current-generation" if replace_generation else "captured-generation"
        service.output_tail, service.last_error = "current output", "current failure"
        log = tmp_path / "captured.log"

        supervisor._capture_output(service, process, "captured-generation", log)

        assert service.output_tail == "current output"
        assert service.last_error == "current failure"
        assert log.read_text(encoding="utf-8") == "old failure token=[redacted]\n"
    finally:
        supervisor.rpc.close()


def test_current_process_output_is_sanitized_and_becomes_its_failure_detail(tmp_path):
    supervisor = Supervisor(tmp_path)
    try:
        process = SimpleNamespace(stdout=io.StringIO("current failure password=secret\n"), poll=lambda: 1, returncode=1)
        service = supervisor.services["telegram"]
        service.process, service.generation = process, "current-generation"

        supervisor._capture_output(service, process, service.generation, tmp_path / "current.log")

        assert service.output_tail == "current failure password=[redacted]"
        assert service.last_error == service.output_tail
    finally:
        supervisor.rpc.close()


def test_new_generation_clears_old_failure_before_rpc_allocation(tmp_path, monkeypatch):
    supervisor = Supervisor(tmp_path)
    try:
        service = supervisor.services["telegram"]
        service.process = SimpleNamespace(poll=lambda: 1, returncode=1)
        service.generation = "previous-generation"
        service.output_tail, service.last_error = "OSError: Address already in use", "previous failure"

        def unavailable():
            raise OSError("RPC allocation unavailable")

        monkeypatch.setattr(supervisor, "_worker_rpc_port", unavailable)

        status = supervisor.start("telegram")

        assert status["state"] == "failed"
        assert status["last_error"] == "RPC allocation unavailable"
        assert service.output_tail == ""
        assert service.generation != "previous-generation"
        assert service.process is None
    finally:
        supervisor.rpc.close()


def test_immediate_process_exit_does_not_reuse_the_previous_output(tmp_path, monkeypatch):
    supervisor = Supervisor(tmp_path)
    try:
        service = supervisor.services["telegram"]
        service.output_tail = "OSError: Address already in use"
        service.last_error = "previous failure"
        process = SimpleNamespace(pid=999, stdout=io.StringIO(""), poll=lambda: 9, returncode=9)
        monkeypatch.setattr(supervisor_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
        monkeypatch.setattr(supervisor_module, "threading", SimpleNamespace(Thread=lambda **_kwargs: SimpleNamespace(start=lambda: None)))

        status = supervisor.start("telegram")

        assert status["state"] == "failed"
        assert status["last_error"] == "process exited with 9"
        assert service.output_tail == ""
    finally:
        supervisor.rpc.close()
