"""Entrypoint do serviço Cloudflare Tunnel administrado pelo Supervisor."""
from __future__ import annotations

import argparse
from pathlib import Path

from ..rpc import RpcServer
from .service import TunnelService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--onmyoji-root", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1"])
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args(argv)
    service = TunnelService(args.onmyoji_root, args.data_dir)

    def handler(method, params):
        if method == "ping":
            return {"service": "cloudflare-tunnel", "state": "running"}
        if method == "shutdown":
            service.stopped.set()
            return {"state": "stopping"}
        return service.rpc(method, params)

    server = RpcServer(args.host, args.port, args.token, handler)
    try:
        server.serve_forever(service.stopped)
    finally:
        service.close()
        server.close()
    return 1 if service.failed.is_set() else 0


if __name__ == "__main__":
    raise SystemExit(main())
