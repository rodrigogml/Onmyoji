"""Persistência da conta BTG descoberta pelo callback MCP."""
from pathlib import Path

from .client import credential_lock, instance_root, load_profile
from .contracts import digest, require


def persist_account(profile: dict, row: dict) -> None:
    from ..configuration import McpConfiguration
    path = Path(profile["_config"])
    with credential_lock(profile, scope="btg-config:" + str(path.resolve())):
        values = {"account_id": row["accountId"], "account_number": str(row["number"]), "branch_code": str(row["branchCode"])}
        config = McpConfiguration(instance_root(path))
        def update(raw: dict) -> None:
            current = load_profile(path, profile["_profile"])
            require(digest(current) == digest(profile), "config_changed", "Perfil mudou durante a conexão; conecte novamente pelo setup.")
            require(not current["account_id"] or all(current[key] == value for key, value in values.items()), "context_mismatch", "A descoberta não pode substituir uma conta existente.")
            raw.update(values)
        config.update_btg_profile(profile["_profile"], update)
        profile.update(values)
