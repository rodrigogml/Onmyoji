"""Worker fechado de um ciclo de leitura BTG MCP para o supervisor local."""
import argparse
import json
from pathlib import Path

from .exception import BtgError
from .service import BtgService


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onmyoji-root", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args()
    try:
        data = BtgService(args.onmyoji_root).invoke(args.profile, "check_pending_payments", {})
        payload, code = {"ok": True, "data": data}, 0
    except (BtgError, OSError, ValueError) as error:
        payload, code = {"ok": False, "error": {"code": getattr(error, "code", "monitor_failed")}}, 2
    print(json.dumps(payload, ensure_ascii=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
