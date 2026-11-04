"""Integridade, diretório escolhido e rollback do executável gerenciado."""
from __future__ import annotations

from hashlib import sha256
import io
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from onmyoji_daemon.cloudflare_tunnel import TunnelConfiguration, TunnelError
from onmyoji_daemon.cloudflare_tunnel.installer import CloudflaredInstaller, asset_name


@pytest.fixture
def installed(tmp_path, monkeypatch):
    resolver = Mock(); resolver.password_exists.return_value = True
    config = TunnelConfiguration(tmp_path, credentials=resolver)
    config.configure({"account_id": "account", "zone_id": "zone", "hostname": "gateway.example.com", "credential_profile": "vault", "credential_entry_path": "APIs/Tunnel"})
    binary = b"official executable for test"
    digest = "sha256:" + sha256(binary).hexdigest()
    release = {"tag_name": "2026.10.7", "assets": [{"name": "cloudflared-windows-amd64.exe", "size": len(binary), "digest": digest,
               "browser_download_url": "https://github.com/cloudflare/cloudflared/releases/download/2026.10.7/cloudflared-windows-amd64.exe"}]}
    installer = CloudflaredInstaller(config)
    monkeypatch.setattr(installer, "_release", lambda version=None: release)
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.installer.asset_name", lambda: "cloudflared-windows-amd64.exe")
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.installer.urllib.request.urlopen", lambda *args, **kwargs: io.BytesIO(binary))
    def run(command, **kwargs):
        if "cloudflared-install-" in str(command[0]):
            assert Path(command[0]).is_relative_to(installer.configuration.root / "tmp")
        return SimpleNamespace(returncode=0, stdout="cloudflared version 2026.10.7", stderr="")
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.installer.subprocess.run", run)
    return installer, config, release, binary


@pytest.mark.parametrize("system,machine,expected", [
    ("Windows", "AMD64", "cloudflared-windows-amd64.exe"),
    ("Linux", "x86_64", "cloudflared-linux-amd64"),
    ("Linux", "aarch64", "cloudflared-linux-arm64"),
])
def test_asset_map_uses_published_platform_combinations(system, machine, expected):
    assert asset_name(system, machine) == expected


@pytest.mark.parametrize("system,machine", [("Windows", "ARM64"), ("Linux", "386"), ("Darwin", "arm64"), ("Windows", "invalid")])
def test_asset_map_rejects_unsupported_combinations(system, machine):
    with pytest.raises(TunnelError) as captured:
        asset_name(system, machine)
    assert captured.value.code == "unsupported_platform"


def test_installer_uses_official_digest_tmp_staging_and_versioned_destination(installed, tmp_path):
    installer, config, release, binary = installed
    progress = []
    result = installer.install(True, progress=progress.append)
    destination = config.path.parent / "bin" / "versions" / "2026.10.7" / "cloudflared.exe"
    assert Path(result["executable"]) == destination
    assert destination.read_bytes() == binary
    settings = config.settings()
    assert settings["managed_digest"] == release["assets"][0]["digest"]
    assert settings["managed_version"] == "2026.10.7"
    assert settings["previous_executable"] == "cloudflared"
    assert settings["cloudflared_executable"] == str(destination)
    assert "validando executável" in progress
    assert not list((tmp_path / "tmp").iterdir())


def test_unwritable_directory_is_rejected_before_download(installed, monkeypatch):
    installer, config, _release, _binary = installed
    before = config.settings()
    download = Mock()
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.installer.os.access", lambda *_args: False)
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.installer.urllib.request.urlopen", download)
    with pytest.raises(TunnelError) as failure:
        installer.install(True)
    assert failure.value.code == "install_permission_denied"
    download.assert_not_called()
    assert config.settings() == before


def test_install_directory_can_be_chosen_outside_instance(installed, tmp_path):
    installer, config, release, binary = installed
    nested = TunnelConfiguration(tmp_path / "instance", credentials=config.credentials)
    nested.configure(config.settings())
    installer.configuration = nested
    outside = tmp_path / "tools"
    result = installer.install(True, str(outside))
    assert Path(result["executable"]).is_relative_to(outside)
    assert nested.settings()["install_directory"] == str(outside)


def test_installer_requires_confirmation_without_network(installed, monkeypatch):
    installer, _, _, _ = installed
    release = Mock(side_effect=AssertionError("rede não autorizada"))
    monkeypatch.setattr(installer, "_release", release)
    with pytest.raises(TunnelError) as captured:
        installer.install(False)
    assert captured.value.code == "confirmation_required"
    release.assert_not_called()


def test_installer_requires_explicit_adoption_and_preserves_external_file(installed, tmp_path):
    installer, config, _, _ = installed
    external = tmp_path / "external.exe"; external.write_bytes(b"previous external binary")
    config.configure({"cloudflared_executable": str(external)}, validate_credentials=False)
    with pytest.raises(TunnelError) as captured:
        installer.install(True)
    assert captured.value.code == "adoption_required"
    result = installer.install(True, adopt=True)
    assert result["previous_executable"] == str(external)
    assert external.read_bytes() == b"previous external binary"


@pytest.mark.parametrize("field,value", [
    ("digest", "sha256:invalid"), ("digest", None),
    ("browser_download_url", "https://untrusted.example/cloudflared.exe"),
    ("size", 0), ("size", None), ("size", 300 * 1024 * 1024),
])
def test_installer_rejects_unverifiable_metadata_without_changing_active_binary(installed, field, value):
    installer, config, release, _ = installed
    previous = config.path.read_bytes()
    release["assets"][0][field] = value
    with pytest.raises(TunnelError):
        installer.install(True)
    assert config.path.read_bytes() == previous
    assert not (config.path.parent / "bin" / "versions").exists()


def test_checksum_failure_never_publishes_download(installed, tmp_path, monkeypatch):
    installer, config, _, _ = installed
    previous = config.path.read_bytes()
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.installer.urllib.request.urlopen", lambda *args, **kwargs: io.BytesIO(b"corrupted"))
    with pytest.raises(TunnelError) as captured:
        installer.install(True)
    assert captured.value.code == "checksum_mismatch"
    assert config.path.read_bytes() == previous
    assert not list((tmp_path / "tmp").iterdir())


def test_execution_validation_failure_keeps_previous_reference(installed, monkeypatch):
    installer, config, _, _ = installed
    previous = config.path.read_bytes()
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.installer.subprocess.run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="private"))
    with pytest.raises(TunnelError) as captured:
        installer.install(True)
    assert captured.value.code == "invalid_executable"
    assert "private" not in str(captured.value)
    assert config.path.read_bytes() == previous


def test_installer_does_not_overwrite_existing_version(installed):
    installer, config, _, _ = installed
    destination = config.path.parent / "bin" / "versions" / "2026.10.7" / "cloudflared.exe"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"unmanaged binary")
    with pytest.raises(TunnelError) as captured:
        installer.install(True)
    assert captured.value.code == "version_conflict"
    assert destination.read_bytes() == b"unmanaged binary"
    assert config.settings()["cloudflared_executable"] == "cloudflared"


def test_repeated_install_does_not_lose_previous_version(installed):
    installer, config, _, _ = installed
    installer.install(True)
    installer.install(True)
    assert config.settings()["previous_executable"] == "cloudflared"


def test_failed_config_commit_removes_only_new_version(installed, monkeypatch):
    installer, config, _, _ = installed
    original = config.path.read_bytes()
    monkeypatch.setattr(config, "configure", Mock(side_effect=OSError("commit failed")))
    with pytest.raises(TunnelError):
        installer.install(True)
    assert not (config.path.parent / "bin" / "versions" / "2026.10.7" / "cloudflared.exe").exists()
    assert config.path.read_bytes() == original


def test_failed_partial_final_copy_is_cleaned_without_changing_reference(installed, monkeypatch):
    installer, config, _, _ = installed
    original = config.path.read_bytes()
    import shutil
    original_copy = shutil.copyfileobj
    def copy(source, destination):
        if "versions" in str(getattr(destination, "name", "")):
            destination.write(b"partial")
            raise OSError("write failed")
        return original_copy(source, destination)
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.installer.shutil.copyfileobj", copy)
    with pytest.raises(TunnelError):
        installer.install(True)
    assert not (config.path.parent / "bin" / "versions" / "2026.10.7" / "cloudflared.exe").exists()
    assert config.path.read_bytes() == original


def test_selected_release_must_match_verified_release_metadata(installed):
    installer, config, _, _ = installed
    previous = config.path.read_bytes()
    with pytest.raises(TunnelError) as captured:
        installer.install(True, version="2026.10.6")
    assert captured.value.code == "invalid_release"
    assert config.path.read_bytes() == previous


def test_installer_reports_permission_and_space_failures_separately(installed, monkeypatch):
    installer, _, _, _ = installed
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.installer.shutil.disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(TunnelError) as captured:
        installer.install(True)
    assert captured.value.code == "insufficient_space"


def test_rollback_validates_and_swaps_references_without_deleting_versions(installed, tmp_path):
    installer, config, _, binary = installed
    previous = tmp_path / "previous.exe"; previous.write_bytes(binary)
    config.configure({"previous_executable": str(previous), "previous_version": "2026.10.7", "previous_digest": "sha256:" + sha256(binary).hexdigest()}, validate_credentials=False)
    result = installer.rollback(True)
    assert result["executable"] == str(previous)
    assert config.settings()["cloudflared_executable"] == str(previous)
    assert previous.is_file()


def test_rollback_refuses_missing_or_modified_previous_version(installed, tmp_path, monkeypatch):
    installer, config, _, binary = installed
    with pytest.raises(TunnelError) as captured:
        installer.rollback(True)
    assert captured.value.code == "rollback_unavailable"
    previous = tmp_path / "previous.exe"; previous.write_bytes(b"modified")
    config.configure({"previous_executable": str(previous), "previous_version": "2026.10.7", "previous_digest": "sha256:" + sha256(binary).hexdigest()}, validate_credentials=False)
    execute = Mock(side_effect=AssertionError("Binário alterado não pode ser executado."))
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.installer.subprocess.run", execute)
    with pytest.raises(TunnelError) as captured:
        installer.rollback(True)
    assert captured.value.code == "checksum_mismatch"
    execute.assert_not_called()
    with pytest.raises(TunnelError):
        installer.rollback(False)
