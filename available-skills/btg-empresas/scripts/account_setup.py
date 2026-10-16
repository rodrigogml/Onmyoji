"""Persistência dos dados da conta obtidos durante o consentimento BTG."""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from setup_profile_api import simple_load, simple_render, simple_save
from client import credential_lock, load_profile, validate_profile
from contracts import digest, require


def configuration_lock(profile: dict):
    return credential_lock(profile, scope="btg-config:" + str(Path(profile["_config"]).resolve()))


def save_configuration(path: Path, data: dict) -> tuple[bool, str]:
    """Valide perfis e serialize gravações com a descoberta feita pelo callback."""
    profiles = [validate_profile(path, name, value) for name, value in data["profiles"].items()]
    previous = simple_load(path, {})
    for value in previous["profiles"].values():
        workspace = value.get("workspace") if isinstance(value, dict) else None
        if isinstance(workspace, str) and Path(workspace).is_absolute() and not Path(workspace).resolve().is_relative_to(path.resolve().parent.parent):
            profiles.append({"workspace": workspace, "_config": str(path.resolve())})
    with contextlib.ExitStack() as stack:
        workspaces = {os.path.normcase(str(Path(p["workspace"]).resolve())): p for p in profiles}
        for workspace in sorted(workspaces):
            stack.enter_context(configuration_lock(workspaces[workspace]))
        # Um editor iniciado antes do callback não deve apagar a conta descoberta.
        current = simple_load(path, {})
        for name, value in data["profiles"].items():
            existing = current["profiles"].get(name, {})
            if not value.get("account_id") and existing.get("account_id"):
                require(all(value.get(k) == existing.get(k) for k in ("company_id", "environment")), "context_mismatch", "Use outro perfil para mudar empresa ou ambiente de uma conta descoberta.")
                for field in ("account_id", "account_number", "branch_code"):
                    require(not value.get(field) or str(value[field]) == str(existing[field]), "config_changed", "Conta foi descoberta enquanto o perfil era editado. Reabra o perfil antes de alterar filtros.")
                    value[field] = existing[field]
                validate_profile(path, name, value)
        return simple_save(path, data)


def persist_account(profile: dict, row: dict) -> None:
    """Grave apenas identificadores após verificar que o perfil permanece igual."""
    path = Path(profile["_config"])
    with configuration_lock(profile):
        current = load_profile(path, profile["_profile"])
        require(digest(current) == digest(profile), "config_changed", "Perfil mudou durante a conexão; conecte novamente pelo setup.")
        values = {"account_id": row["accountId"], "account_number": str(row["number"]), "branch_code": str(row["branchCode"])}
        require(not current["account_id"] or all(current[k] == v for k, v in values.items()), "context_mismatch", "A descoberta não pode substituir a conta existente.")
        data = simple_load(path, {})
        data["profiles"][profile["_profile"]].update(values)
        validate_profile(path, profile["_profile"], data["profiles"][profile["_profile"]])
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(simple_render(data)); stream.flush(); os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        profile.update(values)
