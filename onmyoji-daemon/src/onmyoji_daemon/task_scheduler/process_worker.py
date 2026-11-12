"""Barreira de contenção: nenhum comando é iniciado antes da liberação pelo pai."""
import json
import subprocess
import sys


def main():
    line = sys.stdin.buffer.readline(1_048_577)
    if not line or len(line) > 1_048_576:
        return 125
    try:
        command = json.loads(line.decode("utf-8"))
        return subprocess.run(command, stdin=subprocess.DEVNULL, check=False).returncode
    except Exception:
        print("Não foi possível iniciar o processo configurado.", file=sys.stderr)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
