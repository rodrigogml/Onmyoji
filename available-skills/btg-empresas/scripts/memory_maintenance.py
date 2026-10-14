"""Backup consistente e restauração conservadora, sem acesso bancário."""
from __future__ import annotations
import argparse
import json
import shutil
import sqlite3
import uuid
from pathlib import Path

from client import context, load_profile
from contracts import BtgError, now, require
from store import Store


def checked_path(root: Path, relative: str) -> Path:
    path = root / relative
    require(path.resolve().is_relative_to(root.resolve()) and path.is_file() and not path.is_symlink(), "invalid_snapshot", "Referência de comprovante inválida.")
    return path


def backup(store: Store) -> Path:
    directory = store.root / "backups"
    require(directory.resolve().is_relative_to(store.root.resolve()), "invalid_path", "Backups devem ficar dentro da memória.")
    directory.mkdir(exist_ok=True)
    target = directory / str(uuid.uuid4())
    target.mkdir()
    with sqlite3.connect(store.path) as source, sqlite3.connect(target / "payments.sqlite3") as destination:
        source.backup(destination)
        require(destination.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "invalid_snapshot", "Backup falhou na verificação de integridade.")
        paths = [row[0] for row in destination.execute("SELECT receiptPath FROM payment WHERE receiptPath IS NOT NULL")]
    for relative in set(paths):
        source = checked_path(store.root, relative)
        output = target / relative
        require(output.resolve().is_relative_to(target.resolve()), "invalid_snapshot", "Comprovante fora do snapshot.")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, output)
    (target / "snapshot.json").write_text(json.dumps({"version": 1, "createdAt": now(), "receipts": len(set(paths))}), encoding="utf-8")
    return target


def restore(store: Store, snapshot: Path, workspace: Path, config_root: Path) -> Path:
    snapshot = snapshot.resolve()
    workspace = workspace.resolve()
    require(snapshot.is_relative_to((store.root / "backups").resolve()) and (snapshot / "snapshot.json").is_file(), "invalid_snapshot", "Use um snapshot completo deste workspace.")
    require(workspace.is_absolute() and not workspace.is_relative_to(config_root.resolve()), "invalid_workspace", "Destino deve ser externo ao CODEX_HOME.")
    target = workspace / ".onmyoji" / "btg-empresas"
    require(target.resolve().is_relative_to(workspace) and not target.exists(), "restore_destination_exists", "Restauração exige destino novo; memória existente não será sobrescrita.")
    source = checked_path(snapshot, "payments.sqlite3")
    with sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True) as con:
        require(con.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and con.execute("PRAGMA user_version").fetchone()[0] == 1, "invalid_snapshot", "Snapshot inválido ou versão incompatível.")
        receipt_paths = [row[0] for row in con.execute("SELECT receiptPath FROM payment WHERE receiptPath IS NOT NULL")]
    for relative in receipt_paths:
        checked_path(snapshot, relative)
    target.mkdir(parents=True)
    shutil.copyfile(source, target / "payments.sqlite3")
    for relative in set(receipt_paths):
        output = target / relative
        require(output.resolve().is_relative_to(target), "invalid_snapshot", "Comprovante fora do destino.")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(checked_path(snapshot, relative), output)
    with sqlite3.connect(target / "payments.sqlite3") as con:
        con.row_factory = sqlite3.Row
        for row in con.execute("SELECT * FROM payment").fetchall():
            fields = {"leaseOwner": None, "leaseUntil": None, "updatedAt": now()}
            if row["submissionState"] in {"prepared", "sending", "unknown", "accepted"}:
                # Backup antigo pode anteceder envio já efetivado no banco.
                fields.update(submissionState="unknown", submittedAt=row["submittedAt"] or now(), nextCheckAt=now(), lastError="restore_requires_bank_recovery")
            if row["cancellationState"] == "sending":
                fields["cancellationState"] = "unknown"
            con.execute("UPDATE payment SET " + ",".join(key + "=?" for key in fields) + " WHERE id=?", (*fields.values(), row["id"]))
            Store.event(con, row["id"], "memory.restored", {"requiresBankRecovery": fields.get("submissionState") == "unknown"})
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--confirm")
    args = parser.parse_args()
    try:
        require(args.confirm == args.action.upper(), "confirmation_required", "Confirme a manutenção com BACKUP ou RESTORE.")
        profile = load_profile(args.config, args.profile)
        store = Store(Path(profile["workspace"]), context(profile), args.profile)
        if args.action == "backup":
            target = backup(store)
        else:
            require(args.snapshot is not None and args.destination is not None and args.destination.is_absolute(), "invalid_request", "Informe snapshot e destino absoluto novo.")
            target = restore(store, args.snapshot, args.destination, args.config.resolve().parent.parent)
        result = {"ok": True, "path": str(target), "scope": "all-contexts-in-this-workspace", "action": args.action}
        code = 0
    except (BtgError, OSError, sqlite3.Error, ValueError):
        result, code = {"ok": False, "error": "maintenance_failed", "message": "Manutenção falhou; nenhuma escrita bancária foi executada. Confira snapshot, destino e confirmação."}, 2
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
