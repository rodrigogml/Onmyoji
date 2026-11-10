from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from onmyoji_daemon import linux_service, management
from onmyoji_daemon.linux_service import ServiceLimits


def test_recommended_limits_reserve_capacity_and_scale_with_the_machine():
    limits = linux_service.recommended_limits(cpu_count=8, memory_bytes=16 * 1024 ** 3, swap_bytes=2 * 1024 ** 3)

    assert limits.cpu_quota_percent == 700
    assert limits.memory_high_bytes == 16 * 1024 ** 3 * 60 // 100 // linux_service.MIB * linux_service.MIB
    assert limits.memory_max_bytes == 12 * 1024 ** 3
    assert limits.memory_swap_max_bytes == 2 * 1024 ** 3
    assert limits.tasks_max == 1024
    assert limits.io_weight == 80


def test_service_limits_validate_relationships_and_render_only_selected_controls():
    with pytest.raises(ValueError, match="MemoryHigh"):
        ServiceLimits.from_mapping({"memory_high_bytes": 200, "memory_max_bytes": 100})
    with pytest.raises(ValueError, match="IOWeight"):
        ServiceLimits.from_mapping({"io_weight": 10001})
    with pytest.raises(ValueError, match="private_devices"):
        ServiceLimits.from_mapping({"private_devices": "false"})

    lines = ServiceLimits(cpu_quota_percent=300, tasks_max=512, private_devices=True).systemd_lines()

    assert lines == ["CPUQuota=300%", "TasksAccounting=yes", "TasksMax=512", "PrivateDevices=yes"]


def test_isolated_systemd_unit_exposes_only_registered_trees_and_applies_hardening(tmp_path):
    root = tmp_path / "Shikigami Teste"; workspace = tmp_path / "Shikigami Teste Work"; home = Path("/home/shikigami-teste")
    root.mkdir(); workspace.mkdir()

    unit = management._systemd_unit(root, "/opt/python test/bin/python", Path("/opt/onmyoji src"), "Daemon", "system", "shikigami-teste", "shikigami-teste", workspace=workspace, home=home, isolated=True, limits=ServiceLimits(cpu_quota_percent=200, memory_max_bytes=1024))

    assert "User=shikigami-teste" in unit and "Group=shikigami-teste" in unit
    assert 'Environment="HOME=' in unit and "shikigami-teste" in unit
    assert "ProtectSystem=strict" in unit and "ProtectHome=tmpfs" in unit
    assert "NoNewPrivileges=yes" in unit and "CapabilityBoundingSet=" in unit
    assert f"BindPaths={management._systemd_quote(root.resolve())}" in unit
    assert f"BindPaths={management._systemd_quote(workspace.resolve())}" in unit
    assert "CPUQuota=200%" in unit and "MemoryMax=1024" in unit
    assert 'ExecStart="/opt/python test/bin/python"' in unit


@pytest.mark.parametrize("share", [True, False])
def test_account_provisioning_uses_inherited_acl_or_exclusive_ownership(tmp_path, monkeypatch, share):
    root = tmp_path / "instance"; workspace = tmp_path / "work"; root.mkdir(); workspace.mkdir()
    calls = []
    monkeypatch.setattr(linux_service.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(linux_service.os, "geteuid", lambda: 1000, raising=False)

    def run(command, **kwargs):
        calls.append(command)
        executable_index = 2 if command[:2] == ["sudo", "--"] else 0
        normalized = [Path(command[executable_index]).name, *command[executable_index + 1:]]
        code = 1 if normalized[:3] in (["getent", "passwd", "shikigami-teste"], ["getent", "group", "shikigami-teste"]) else 0
        return subprocess.CompletedProcess(command, code, "", "")

    monkeypatch.setattr(linux_service.subprocess, "run", run)

    ok, message, home = linux_service.provision_account("shikigami-teste", "instalador", root, workspace, True, share_with_installer=share)

    assert ok, message
    assert home == Path("/home/shikigami-teste")
    assert any(command[:2] == ["sudo", "--"] and Path(command[2]).name == "useradd" for command in calls)
    assert any(any(Path(value).name == "chmod" for value in command) and "0700" in command and str(home) in command for command in calls)
    if share:
        assert any(any(Path(value).name == "usermod" for value in command) and "instalador" in command for command in calls)
        assert any(any(Path(value).name == "setfacl" for value in command) and "d:u:instalador:rwx,d:u::rwx,d:g::rwx,d:m::rwx,d:o::---" in command for command in calls)
        assert any(any(Path(value).name == "chown" for value in command) and "-R" in command and "shikigami-teste:shikigami-teste" in command for command in calls)
    else:
        assert not any(any(Path(value).name == "usermod" for value in command) for command in calls)
        assert any(any(Path(value).name == "chown" for value in command) and "shikigami-teste:shikigami-teste" in command for command in calls)


def test_account_name_is_stable_and_linux_safe():
    assert linux_service.account_name("  Tsuki da Silva  ") == "shikigami-tsuki-da-silva"
    assert linux_service.account_name("Ōkami") == "shikigami-okami"
    with pytest.raises(ValueError):
        linux_service.validate_account("Conta Inválida")


def test_linux_tool_finds_administrative_binary_outside_user_path(tmp_path, monkeypatch):
    administrative = tmp_path / "sbin"; administrative.mkdir()
    useradd = administrative / "useradd"; useradd.write_text("", encoding="utf-8")
    monkeypatch.setattr(linux_service.shutil, "which", lambda _name: None)
    monkeypatch.setattr(linux_service, "ADMINISTRATIVE_TOOL_DIRECTORIES", (administrative,))

    assert linux_service.linux_tool("useradd") == str(useradd)
    assert linux_service.linux_tool("missing") is None
    monkeypatch.setattr(linux_service.shutil, "which", lambda _name: str(tmp_path / "untrusted" / "useradd"))
    assert linux_service.linux_tool("useradd") == str(useradd)


def test_execution_profile_round_trip_stays_inside_private_configuration(tmp_path):
    linux_service.save_execution_profile(tmp_path, {"mode": "dedicated", "account": "shikigami-teste", "validated": False})

    assert linux_service.execution_profile_path(tmp_path) == tmp_path / "configs" / "shikigami" / "linux-execution.json"
    assert linux_service.load_execution_profile(tmp_path) == {"schema_version": 1, "mode": "dedicated", "account": "shikigami-teste", "validated": False}


def test_account_command_sets_dedicated_home_and_environment_without_login_shell(monkeypatch):
    monkeypatch.setattr(linux_service.shutil, "which", lambda name: "/usr/bin/sudo" if name == "sudo" else None)

    command = linux_service.account_command("shikigami-teste", Path("/home/shikigami-teste"), ["/usr/bin/python3", "setup.py"], {"CODEX_HOME": "/srv/instancia"})

    assert command[:6] == ["sudo", "--user", "shikigami-teste", "--set-home", "--", "env"]
    assert command[6].replace("\\", "/") == "HOME=/home/shikigami-teste"
    assert "CODEX_HOME=/srv/instancia" in command
    assert command[-2:] == ["/usr/bin/python3", "setup.py"]
