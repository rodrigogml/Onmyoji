"""Cliente interativo local que sempre inicia Codex via App Server."""
from __future__ import annotations

import argparse
import os
import shutil
import threading
import tomllib
from pathlib import Path

from .instructions import InstructionComposer
from .telegram import CodexAppServer, CodexProtocolError, Settings


def _bootstrap(root: Path, data_dir: Path) -> None:
    """Permite iniciar a sessão local antes de configurar o Telegram."""
    config = root / "shikigami" / "daemon" / "telegram.toml"
    if not config.exists():
        config.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(__file__).resolve().parents[2] / "configs" / "telegram.toml.model", config)
    instructions = root / "shikigami" / "instructions.md"
    if not instructions.exists():
        instructions.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(__file__).resolve().parents[2] / "configs" / "shikigami.md.model", instructions)


def _approval_policy(settings: Settings) -> str:
    """Traduz a política salva para o valor esperado pelo App Server."""
    return {"on-request": "onRequest", "never": "never"}.get(settings.approval, "never")


def _sandbox_policy(settings: Settings) -> dict:
    """Converte sandbox e raízes adicionais locais para o contrato do App Server."""
    if settings.sandbox == "danger-full-access": return {"type": "dangerFullAccess"}
    if settings.sandbox == "read-only": return {"type": "readOnly", "networkAccess": False}
    workspace = settings.project.resolve()
    roots = [str(workspace)]
    try:
        system = tomllib.loads((settings.root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8"))
        for value in system.get("codex", {}).get("additional_writable_directories", []):
            if not isinstance(value, str): continue
            directory = Path(value).expanduser()
            roots.append(str((workspace / directory).resolve() if not directory.is_absolute() else directory.resolve()))
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        pass
    return {"type": "workspaceWrite", "writableRoots": list(dict.fromkeys(roots)), "networkAccess": False}


def _short(value: object, limit: int = 1600) -> str:
    """Prepara detalhes para a tela local sem criar registros persistentes."""
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _permission_response(params: dict, grant: bool, scope: str = "turn") -> dict:
    """Responde somente com o subconjunto de permissões pedido pelo App Server."""
    requested = params.get("permissions")
    if not grant:
        return {"permissions": [] if isinstance(requested, list) else {}}
    if not isinstance(requested, (list, dict)):
        return {"permissions": {}}
    response = {"permissions": requested}
    if scope == "session": response["scope"] = "session"
    return response


def _approval_response(method: str, params: dict, active_thread_id: str, read=input, write=print) -> dict:
    """Coleta uma decisão local para um pedido de aprovação da thread ativa."""
    if params.get("threadId") != active_thread_id:
        return _permission_response(params, False) if method == "item/permissions/requestApproval" else {"decision": "decline"}
    title = {
        "item/commandExecution/requestApproval": "Aprovação de comando",
        "item/fileChange/requestApproval": "Aprovação de alteração de arquivo",
        "item/permissions/requestApproval": "Aprovação de permissões",
    }.get(method)
    if title is None:
        return {"decision": "decline"}
    write("\n" + title)
    if params.get("reason"): write("Motivo: " + _short(params["reason"]))
    if method == "item/commandExecution/requestApproval":
        if params.get("cwd"): write("Diretório: " + _short(params["cwd"]))
        if params.get("command"): write("Comando: " + _short(params["command"]))
        network = params.get("networkApprovalContext")
        if isinstance(network, dict): write("Rede: " + _short(f"{network.get('protocol', '')}://{network.get('host', '')}"))
    elif method == "item/fileChange/requestApproval":
        if params.get("grantRoot"): write("Raiz solicitada: " + _short(params["grantRoot"]))
    else:
        write("Permissões solicitadas: " + _short(params.get("permissions")))
    available = {value for value in params.get("availableDecisions", []) if isinstance(value, str)}
    supports_once = "accept" in available
    supports_session = "acceptForSession" in available
    supports_cancel = "cancel" in available
    if method == "item/permissions/requestApproval":
        supports_once, supports_session = isinstance(params.get("permissions"), (list, dict)), True
    options = "A=aprovar uma vez, N=recusar"
    if supports_session: options = "A=aprovar uma vez, S=aprovar na sessão, N=recusar"
    if supports_cancel: options += ", C=cancelar"
    if not supports_once: options = "N=recusar"
    try: choice = read(f"{options} [N]: ").strip().casefold() or "n"
    except (EOFError, KeyboardInterrupt): choice = "n"
    if choice == "a" and supports_once:
        return _permission_response(params, True) if method == "item/permissions/requestApproval" else {"decision": "accept"}
    if choice == "s" and supports_session:
        return _permission_response(params, True, "session") if method == "item/permissions/requestApproval" else {"decision": "acceptForSession"}
    if choice == "c" and supports_cancel:
        return {"decision": "cancel"}
    return _permission_response(params, False) if method == "item/permissions/requestApproval" else {"decision": "decline"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--onmyoji-root", type=Path, required=True); args = parser.parse_args(argv)
    root = args.onmyoji_root.resolve(); data_dir = root / "configs" / "daemon" / "services" / "telegram"; _bootstrap(root, data_dir)
    settings = Settings.load(root, data_dir)
    composer = InstructionComposer(root, data_dir, settings.instructions_file, settings.instructions_enabled)
    from .instance import identity
    bundle = composer.compose(identity=identity(root), telegram=False)
    client = CodexAppServer(settings); completed, final, active_thread = threading.Event(), {"text": ""}, {"id": ""}
    terminal_lock = threading.Lock()
    def notification(method: str, params: dict) -> None:
        if method == "item/completed":
            item = params.get("item", {}) if isinstance(params, dict) else {}
            if item.get("type") == "agentMessage" and item.get("phase") in {None, "final_answer"}: final["text"] = str(item.get("text") or "")
        if method == "turn/completed": completed.set()
    client.notification_handler = notification
    def request_handler(method: str, params: dict) -> dict:
        with terminal_lock:
            return _approval_response(method, params, active_thread["id"])
    client.request_handler = request_handler
    try:
        parameters = {"cwd": str(settings.project), "approvalPolicy": _approval_policy(settings), "sandboxPolicy": _sandbox_policy(settings), "developerInstructions": bundle.text, "serviceName": "onmyoji_interactive"}
        if settings.model: parameters["model"] = settings.model
        started = client.request("thread/start", parameters)
        thread = started.get("thread") if isinstance(started.get("thread"), dict) else started; thread_id = str(thread.get("id") or "")
        if not thread_id: raise CodexProtocolError("O App Server não retornou uma thread.")
        active_thread["id"] = thread_id
        print(f"Onmyōji interativo — {identity}. Digite /exit para sair.")
        while True:
            try: text = input("› ").strip()
            except (EOFError, KeyboardInterrupt): break
            if text in {"/exit", "/quit"}: break
            if not text: continue
            completed.clear(); final["text"] = ""
            turn = {"threadId": thread_id, "input": [{"type": "text", "text": text}], "cwd": str(settings.project), "approvalPolicy": _approval_policy(settings), "sandboxPolicy": _sandbox_policy(settings), "effort": settings.effort}
            if settings.model: turn["model"] = settings.model
            try: client.request("turn/start", turn)
            except CodexProtocolError as error:
                print(f"! Não foi possível iniciar o turno: {error}")
                continue
            if not completed.wait(settings.turn_timeout): print("! O turno excedeu o tempo configurado.")
            elif final["text"]: print("\n" + final["text"] + "\n")
            else: print("\n! O agente concluiu sem resposta textual.\n")
    finally: client.stop()
    return 0


if __name__ == "__main__": raise SystemExit(main())
