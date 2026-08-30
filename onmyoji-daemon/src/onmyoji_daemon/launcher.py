"""Cliente interativo local que sempre inicia Codex via App Server."""
from __future__ import annotations

import argparse
import threading
from pathlib import Path

from .instructions import InstructionComposer
from .telegram import CodexAppServer, CodexProtocolError, Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--onmyoji-root", type=Path, required=True); args = parser.parse_args(argv)
    root = args.onmyoji_root.resolve(); data_dir = root / "configs" / "daemon" / "services" / "telegram"
    settings = Settings.load(root, data_dir)
    composer = InstructionComposer(root, data_dir, settings.instructions_file, settings.instructions_enabled)
    identity = root.name.removeprefix("Onmyoji-").strip() or "Shikigami"; bundle = composer.compose(identity=identity, telegram=False)
    client = CodexAppServer(settings); completed, final = threading.Event(), {"text": ""}
    def notification(method: str, params: dict) -> None:
        if method == "item/completed":
            item = params.get("item", {}) if isinstance(params, dict) else {}
            if item.get("type") == "agentMessage" and item.get("phase") in {None, "final_answer"}: final["text"] = str(item.get("text") or "")
        if method == "turn/completed": completed.set()
    client.notification_handler = notification
    try:
        started = client.request("thread/start", {"cwd": str(settings.project), "approvalPolicy": settings.approval, "sandbox": settings.sandbox, "developerInstructions": bundle.text, "serviceName": "onmyoji_interactive"})
        thread = started.get("thread") if isinstance(started.get("thread"), dict) else started; thread_id = str(thread.get("id") or "")
        if not thread_id: raise CodexProtocolError("O App Server não retornou uma thread.")
        print(f"Onmyōji interativo — {identity}. Digite /exit para sair.")
        while True:
            try: text = input("› ").strip()
            except (EOFError, KeyboardInterrupt): break
            if text in {"/exit", "/quit"}: break
            if not text: continue
            completed.clear(); final["text"] = ""
            current = composer.compose(identity=identity, telegram=False)
            client.request("thread/resume", {"threadId": thread_id, "cwd": str(settings.project), "approvalPolicy": settings.approval, "sandbox": settings.sandbox, "developerInstructions": current.text})
            client.request("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": text}], "cwd": str(settings.project), "approvalPolicy": settings.approval, "sandboxPolicy": {"type": "workspaceWrite", "writableRoots": [str(settings.project)], "networkAccess": False}})
            if not completed.wait(settings.turn_timeout): print("! O turno excedeu o tempo configurado.")
            elif final["text"]: print("\n" + final["text"] + "\n")
            else: print("\n! O agente concluiu sem resposta textual.\n")
    finally: client.stop()
    return 0


if __name__ == "__main__": raise SystemExit(main())
