from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import venv
import zipfile

import pytest

from onmyoji_daemon import runtime, runtime_recovery as recovery


DETAIL = "error: uninstall-no-record-file\nCannot uninstall Example_Package 1.0\nno RECORD file was found"


def protected_metadata(**changes):
    return {"name": "example-package", "version": "1.0", "installer": "debian", "missing_record": True, "safe_target": True, **changes}


def candidate_report(**changes):
    candidate = {"metadata": {"name": "example-package", "version": "2.0"}, "download_info": {"url": "https://packages.example.test/example_package-2.0-py3-none-any.whl", "archive_info": {"hashes": {"sha256": "ab12"}}}, **changes}
    return {"install": [candidate]}


def patch_metadata(monkeypatch, metadata=None):
    def run(command, **kwargs):
        assert command[1:4] == ["-I", "-B", "-c"]
        assert kwargs["env"]["PYTHONNOUSERSITE"] == "1"
        return subprocess.CompletedProcess(command, 0, json.dumps(protected_metadata() if metadata is None else metadata), "")
    monkeypatch.setattr(recovery.subprocess, "run", run)


def test_recovers_only_exact_candidate_and_preserves_original_requirements(tmp_path, monkeypatch):
    """Somente o bloqueador é sobreposto; o candidato mantém URL e hash resolvidos."""
    patch_metadata(monkeypatch)
    calls = []
    messages = []
    recovered = set()
    requirements = ["application>=1,<2", "another-library"]
    report_paths = []
    def install(arguments, stage):
        calls.append(arguments)
        if "--report" in arguments:
            report = Path(arguments[arguments.index("--report") + 1])
            report_paths.append(report)
            assert report.is_relative_to(tmp_path / "tmp")
            report.write_text(json.dumps(candidate_report()), encoding="utf-8")
        return 0, ""

    assert recovery.recover_distribution(tmp_path, Path(sys.executable), requirements, DETAIL, messages.append, install, recovered)
    assert calls[0][-2:] == requirements
    assert "--dry-run" in calls[0] and "--ignore-installed" not in calls[0]
    assert calls[1] == ["install", "--disable-pip-version-check", "--no-input", "--ignore-installed", "--no-deps", "example-package @ https://packages.example.test/example_package-2.0-py3-none-any.whl#sha256=ab12"]
    assert recovered == {"example-package"}
    assert len(messages) == 1 and "sem remover a original" in messages[0]
    assert all(not path.exists() for path in report_paths)


@pytest.mark.parametrize("detail, recovered", [("Permission denied", set()), ("RECORD file not found", set()), (DETAIL, {"example-package"})])
def test_unrecognized_or_repeated_failure_does_not_attempt_recovery(tmp_path, detail, recovered):
    def unexpected(*_args, **_kwargs):
        pytest.fail("A falha não autoriza nova sobreposição.")
    assert recovery.recover_distribution(tmp_path, Path(sys.executable), ["example"], detail, unexpected, unexpected, recovered) is False


@pytest.mark.parametrize("changes", [{"safe_target": False}, {"missing_record": False}, {"installer": "pip"}, {"installer": ""}, {"name": "other"}, {"version": "0.9"}])
def test_refuses_unsafe_metadata_without_installing(tmp_path, monkeypatch, changes):
    """Mesmo destino, falta de precedência e metadados inconsistentes não são reparados à força."""
    patch_metadata(monkeypatch, protected_metadata(**changes))
    with pytest.raises(runtime.RuntimeError, match="Não é seguro.*isolated"):
        recovery.recover_distribution(tmp_path, Path(sys.executable), ["example"], DETAIL, None, lambda *_args: pytest.fail("Metadados inseguros não podem instalar."), set())


@pytest.mark.parametrize("result", [subprocess.CompletedProcess([], 1, "", "error"), subprocess.CompletedProcess([], 0, "invalid-json", ""), OSError("missing"), subprocess.TimeoutExpired("python", 15)])
def test_reports_metadata_probe_errors(tmp_path, monkeypatch, result):
    def run(*_args, **_kwargs):
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(recovery.subprocess, "run", run)
    with pytest.raises(runtime.RuntimeError, match="isolated"):
        recovery.recover_distribution(tmp_path, Path(sys.executable), ["example"], DETAIL, None, lambda *_args: pytest.fail("Uma consulta inválida não pode instalar."), set())


@pytest.mark.parametrize("failure", ["resolve", "missing-report", "invalid-report", "missing-candidate", "install"])
def test_failed_resolution_or_overlay_never_marks_distribution_recovered(tmp_path, monkeypatch, failure):
    patch_metadata(monkeypatch)
    recovered = set()
    reports = []
    def install(arguments, stage):
        if "--report" in arguments:
            report = Path(arguments[arguments.index("--report") + 1])
            reports.append(report)
            if failure == "resolve":
                return 1, "token=hidden resolver failure"
            if failure != "missing-report":
                report.write_text("invalid" if failure == "invalid-report" else json.dumps({"install": []} if failure == "missing-candidate" else candidate_report()), encoding="utf-8")
            return 0, ""
        return 1, "password=hidden overlay failure"
    with pytest.raises(runtime.RuntimeError, match="isolated") as raised:
        recovery.recover_distribution(tmp_path, Path(sys.executable), ["example"], DETAIL, None, install, recovered)
    assert "hidden" not in str(raised.value)
    assert not recovered
    assert all(not path.exists() for path in reports)


@pytest.mark.parametrize("report, expected", [
    ({"install": []}, None),
    (candidate_report(metadata={"name": "other", "version": "2.0"}), None),
    (candidate_report(metadata={"name": "example-package"}), None),
    (candidate_report(download_info={"url": "ssh://example.test/package.whl"}), None),
    (candidate_report(download_info={"url": "https:"}), None),
    (candidate_report(download_info={"url": "file:///package.whl", "archive_info": {"hash": "sha256=ab12"}}), "example-package @ file:///package.whl#sha256=ab12"),
    (candidate_report(download_info={"url": "file:///package.whl"}), "example-package @ file:///package.whl"),
    (candidate_report(download_info={"url": "file:///package.whl#subdirectory=src", "archive_info": {"hashes": {"sha512": "ab12"}}}), "example-package @ file:///package.whl#subdirectory=src&sha512=ab12"),
    (candidate_report(download_info={"url": "file:///package.whl", "archive_info": {"hashes": {"invalid!": "ab12"}}}), None),
    (candidate_report(download_info={"url": "file:///package.whl", "archive_info": {"hashes": {"sha256": "not-hex"}}}), None),
])
def test_candidate_reference_keeps_only_valid_resolved_downloads(report, expected):
    assert recovery._candidate_reference(report, "example-package") == expected


def test_temporary_staging_failure_has_runtime_diagnostic(tmp_path, monkeypatch):
    patch_metadata(monkeypatch)
    (tmp_path / "tmp").write_text("cannot be a directory", encoding="utf-8")
    with pytest.raises(runtime.RuntimeError, match="relatório temporário.*isolated"):
        recovery.recover_distribution(tmp_path, Path(sys.executable), ["example"], DETAIL, None, lambda *_args: pytest.fail("Um staging indisponível não pode instalar."), set())


def test_real_pip_recovers_external_distribution_without_record_offline(tmp_path, monkeypatch):
    """A cópia nova prevalece em imports e metadados, preservando a instalação protegida."""
    instance = tmp_path / "instance"
    instance.mkdir()
    environment_root = tmp_path / "python"
    for name in ("TMP", "TEMP", "TMPDIR"):
        monkeypatch.setenv(name, str(tmp_path))
    venv.create(environment_root, with_pip=True)
    executable = environment_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    environment = runtime._environment()
    environment.update(TMP=str(tmp_path), TEMP=str(tmp_path), TMPDIR=str(tmp_path))
    site = Path(subprocess.check_output(runtime._python_code(executable, "import sysconfig; print(sysconfig.get_path('purelib'))"), env=environment, text=True).strip())
    external = environment_root / "debian-packages"
    package = external / "onmyoji_record_probe"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    metadata = external / "onmyoji_record_probe-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: onmyoji-record-probe\nVersion: 1.0\n", encoding="utf-8")
    (metadata / "INSTALLER").write_text("debian\n", encoding="utf-8")
    (site / "external-debian.pth").write_text(str(external) + "\n", encoding="utf-8")
    original = {str(path.relative_to(external)): path.read_bytes() for path in external.rglob("*") if path.is_file()}
    wheel = tmp_path / "onmyoji_record_probe-2.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("onmyoji_record_probe/__init__.py", "VALUE = 2\n")
        archive.writestr("onmyoji_record_probe-2.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: onmyoji-record-probe\nVersion: 2.0\n")
        archive.writestr("onmyoji_record_probe-2.0.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr("onmyoji_record_probe-2.0.dist-info/RECORD", "")
    calls = []
    def install(arguments, stage):
        calls.append(arguments)
        completed = subprocess.run(runtime._pip_command(executable, [*arguments, "--no-index", "--no-cache-dir"]), env=environment, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=60, check=False)
        return completed.returncode, completed.stdout + completed.stderr
    requirements = [str(wheel)]
    code, detail = install(["install", "--disable-pip-version-check", "--no-input", "--upgrade", *requirements], "install")
    assert code != 0 and "RECORD" in detail
    recovered = set()

    assert recovery.recover_distribution(instance, executable, requirements, detail, None, install, recovered)
    assert install(["install", "--disable-pip-version-check", "--no-input", "--upgrade", *requirements], "retry")[0] == 0

    probe = "import json, onmyoji_record_probe; from importlib.metadata import version; print(json.dumps([onmyoji_record_probe.VALUE, version('onmyoji-record-probe')]))"
    assert json.loads(subprocess.check_output(runtime._python_code(executable, probe), env=environment, text=True)) == [2, "2.0"]
    assert {str(path.relative_to(external)): path.read_bytes() for path in external.rglob("*") if path.is_file()} == original
    assert not (metadata / "RECORD").exists()
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() in next(arguments[-1] for arguments in calls if "--ignore-installed" in arguments)
