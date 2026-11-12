"""Entrada do Gateway supervisionado pelo daemon."""
from __future__ import annotations

import argparse
import threading
from pathlib import Path

from ..rpc import RpcServer
from .service import PublicGateway


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--onmyoji-root", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--http-port", type=int)
    args = parser.parse_args(argv)
    gateway = PublicGateway(args.onmyoji_root, args.data_dir, args.token, args.http_port)
    stopped = threading.Event()
    def handler(method, params):
        if method == "ping":
            return {"service": "gateway", "state": "running", "ready": gateway.ready}
        if method == "shutdown":
            stopped.set()
            return {"state": "stopping"}
        return gateway.rpc(method, params)
    try:
        gateway.start_http()
        server = RpcServer(args.host, args.port, args.token, handler)
        try:
            server.serve_forever(stopped)
        finally:
            server.close()
    finally:
        gateway.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
