"""Transporte privado do App Server com correlação e drenagem não bloqueante.

Cada abertura possui um epoch; leitores e respostas de ferramentas conservam
essa identidade mesmo quando outro processo já tiver sido aberto. Handlers de
canal nunca são executados pelos leitores de stdout/stderr.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
import queue
import re
import secrets
import subprocess
import threading
import time
from typing import Any

from .codex_execution import CodexExecutionError, CodexLaunch


def _safe_detail(value: object) -> str:
    text = str(value).replace("\n", " ")
    text = re.sub(r"https?://\S+", "[endpoint]", text)
    return re.sub(r"(?i)(token|password|secret|api[_-]?key)\s*[=:]\s*\S+", r"\1=[redacted]", text)[-500:]


def _rpc_id(value: object) -> bool:
    return isinstance(value, str) or isinstance(value, int) and not isinstance(value, bool)


class CodexProtocolError(RuntimeError):
    """Falha de protocolo com certeza de aceitação explícita.

    ``not_sent`` prova que a escrita não começou; ``rejected`` representa uma
    recusa JSON-RPC. ``uncertain`` e ``transport_lost`` não autorizam repetir
    uma operação possivelmente aceita. O ID da RPC só correlaciona respostas.
    """

    def __init__(self, message: str, *, kind: str = "uncertain", method: str | None = None,
                 request_id: int | str | None = None, process_epoch: str | None = None,
                 written: bool = False, rpc_code: int | None = None, rpc_data: Any = None):
        super().__init__(_safe_detail(message))
        self.kind, self.method, self.request_id = kind, method, request_id
        self.process_epoch, self.written = process_epoch, written
        self.rpc_code, self.rpc_data = rpc_code, rpc_data

    @property
    def acceptance_unknown(self) -> bool:
        return self.kind not in {"not_sent", "rejected"}

    @property
    def definitive_rejection(self) -> bool:
        return self.kind == "rejected"


@dataclass
class _Outbound:
    payload: dict[str, Any]
    method: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    done: threading.Event = field(default_factory=threading.Event)
    started: bool = False
    cancelled: bool = False
    error: CodexProtocolError | None = None


@dataclass
class _Pending:
    method: str
    epoch: str
    response: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=1))
    outbound: _Outbound | None = None


@dataclass
class _Runtime:
    process: Any
    epoch: str
    outgoing: queue.Queue
    events: list[queue.Queue]
    requests: queue.Queue
    closed: threading.Event = field(default_factory=threading.Event)
    ready: threading.Event = field(default_factory=threading.Event)
    reaped: threading.Event = field(default_factory=threading.Event)
    request_lock: threading.Lock = field(default_factory=threading.Lock)
    request_ids: dict[int | str, str] = field(default_factory=dict)
    active_requests: int = 0
    request_workers_started: int = 0
    active_events: int = 0
    event_condition: threading.Condition = field(default_factory=threading.Condition)
    pending_events: int = 0
    frames_inflight: int = 0
    writer_active: bool = False
    sequence: int = 0
    routes: dict[str, int] = field(default_factory=dict)
    error: CodexProtocolError | None = None


class CodexAppServer:
    """Processo persistente com filas limitadas e handlers isolados do leitor.

    ``event_handler(method, params, context)`` e ``request_event_handler`` são
    as interfaces com epoch/sequence; handlers legados de dois argumentos
    permanecem disponíveis. Notificações da mesma thread preservam ordem.
    Overflow ou perda do transporte produz ``transport/lost`` e interrompe
    admissão daquele epoch; nenhuma falha de ACK causa retry automático.
    """

    def __init__(self, settings: Any, *, event_limit: int = 1024, request_limit: int = 64,
                 event_workers: int = 4, request_workers: int | None = None,
                 max_frame_bytes: int = 4 * 1024 * 1024, event_drain_timeout: float = 1):
        limits = (event_limit, request_limit, event_workers, max_frame_bytes) + (() if request_workers is None else (request_workers,))
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in limits):
            raise ValueError("App Server transport limits must be positive")
        if isinstance(event_drain_timeout, bool) or not isinstance(event_drain_timeout, (int, float)) or not math.isfinite(event_drain_timeout) or event_drain_timeout < 0:
            raise ValueError("App Server transport drain timeout must be finite and nonnegative")
        self.settings = settings
        self.process = self.reader = self.stderr_reader = None
        self.write_lock, self.pending_lock = threading.Lock(), threading.Lock()
        self._lifecycle = threading.Lock()
        self.pending: dict[int, _Pending] = {}
        self.next_id = 1
        self.notification_handler = self.request_handler = None
        self.event_handler = self.request_event_handler = None
        self.last_error: str | None = None
        self.process_epoch: str | None = None
        self.state = "stopped"
        self._runtime: _Runtime | None = None
        self._runtimes: dict[str, _Runtime] = {}
        self._starting: threading.Event | None = None
        self._start_error: CodexProtocolError | None = None
        self.event_limit, self.request_limit = event_limit, request_limit
        self.event_workers = min(event_workers, event_limit)
        self.request_workers, self.max_frame_bytes = min(request_workers or request_limit, request_limit), max_frame_bytes
        self.event_drain_timeout = event_drain_timeout

    @property
    def epoch(self) -> str | None:
        return self.process_epoch

    @property
    def pending_count(self) -> int:
        with self.pending_lock:
            return len(self.pending)

    @property
    def busy(self) -> bool:
        runtime = self._runtime
        if self.pending_count or self.state == "starting":
            return True
        return bool(runtime and (runtime.active_requests or runtime.pending_events or runtime.frames_inflight or runtime.writer_active or not runtime.outgoing.empty()
                                 or any(not lane.empty() for lane in runtime.events)))

    @property
    def draining(self) -> bool:
        return self.state in {"draining", "lost"}

    def running(self) -> bool:
        runtime = self._runtime
        return bool(runtime and not runtime.closed.is_set() and runtime.process.poll() is None)

    def wait_events(self, process_epoch: str, timeout: float = 1) -> bool:
        """Aguarda eventos já admitidos do epoch sem atrasar falha de RPC.

        Usar ao recuperar transporte perdido, antes de descartar o registro do
        turno. Inclui frame que já estava em despacho e handlers em execução;
        não espera ferramentas/HTTP agendados por esses handlers. ``False``
        indica prazo esgotado ou epoch antigo não mais retido, nunca prova que
        o turno falhou. O leitor e o reaper não aguardam esta barreira.
        """
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("Event drain timeout must be finite and nonnegative")
        with self._lifecycle:
            runtime = self._runtimes.get(process_epoch)
        return bool(runtime and self._wait_runtime_events(runtime, timeout))

    @staticmethod
    def _wait_runtime_events(runtime: _Runtime, timeout: float) -> bool:
        with runtime.event_condition:
            return runtime.event_condition.wait_for(lambda: not runtime.pending_events and not runtime.frames_inflight, timeout)

    def start(self) -> None:
        with self._lifecycle:
            if self.state == "initialized" and self.running():
                return
            if self.state == "draining":
                raise CodexProtocolError("Codex App Server está encerrando", kind="not_sent")
            if self._starting is not None:
                waiter, leader = self._starting, False
            else:
                waiter, leader = threading.Event(), True
                self._starting, self._start_error, self.state = waiter, None, "starting"
        if not leader:
            if not waiter.wait(45):
                raise CodexProtocolError("Codex App Server excedeu o tempo de inicialização", kind="not_sent")
            if self._start_error:
                raise self._start_error
            if not self.running():
                raise CodexProtocolError("Codex App Server não está disponível", kind="not_sent")
            return
        runtime = None
        try:
            previous = self._runtime
            if previous and previous.closed.is_set() and previous.process.poll() is None:
                previous.reaped.wait(11)
                if previous.process.poll() is None:
                    raise CodexProtocolError("O processo App Server anterior ainda não encerrou", kind="not_sent", process_epoch=previous.epoch)
            command, workspace, environment = CodexLaunch.from_system(self.settings.root).app_server_launch()
            process = subprocess.Popen(command, cwd=workspace, env=environment, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       encoding="utf-8", errors="replace", bufsize=1,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            lane_sizes = [self.event_limit // self.event_workers + (index < self.event_limit % self.event_workers)
                          for index in range(self.event_workers)]
            runtime = _Runtime(process, secrets.token_urlsafe(18), queue.Queue(maxsize=self.request_limit * 2),
                               [queue.Queue(maxsize=size) for size in lane_sizes], queue.Queue(maxsize=self.request_limit))
            with self._lifecycle:
                self._runtime, self.process, self.process_epoch = runtime, process, runtime.epoch
                self._runtimes[runtime.epoch] = runtime
                while len(self._runtimes) > 8:
                    self._runtimes.pop(next(iter(self._runtimes)))
            self._launch_workers(runtime)
            self._request_runtime(runtime, "initialize", {"clientInfo": {"name": "onmyoji_execution_gateway", "title": "Onmyoji Execution Gateway", "version": "1.0"}, "capabilities": {"experimentalApi": True}}, 45)
            self._send_runtime(runtime, {"method": "initialized", "params": {}}, 45)
            with self._lifecycle:
                if runtime.closed.is_set() or self._runtime is not runtime:
                    raise runtime.error or CodexProtocolError("Codex App Server encerrou durante initialize")
                self.state = "initialized"
                runtime.ready.set()
        except (CodexExecutionError, OSError, CodexProtocolError) as error:
            failure = error if isinstance(error, CodexProtocolError) else CodexProtocolError(str(error), kind="not_sent")
            self._start_error = failure
            if runtime:
                self._lose(runtime, "initialization_failed")
            else:
                self.state = "lost"
            raise failure
        finally:
            with self._lifecycle:
                if self._starting is waiter:
                    self._starting = None
                waiter.set()

    def _launch_workers(self, runtime: _Runtime) -> None:
        threading.Thread(target=self._writer, args=(runtime,), daemon=True).start()
        for lane in runtime.events:
            threading.Thread(target=self._event_worker, args=(runtime, lane), daemon=True).start()
        self.reader = threading.Thread(target=self._read_stdout, args=(runtime,), daemon=True)
        self.stderr_reader = threading.Thread(target=self._read_stderr, args=(runtime,), daemon=True)
        self.reader.start()
        self.stderr_reader.start()

    def stop(self) -> None:
        runtime = self._runtime
        if not runtime:
            return
        self.state = "draining"
        self._lose(runtime, "stopped")
        runtime.reaped.wait(11)
        if self._runtime is runtime:
            if runtime.process.poll() is not None:
                self.process, self.state = None, "stopped"
            else:
                self.state = "lost"

    def _enqueue(self, runtime: _Runtime, outbound: _Outbound) -> None:
        if runtime.closed.is_set() or self._runtime is not runtime:
            raise CodexProtocolError("Codex App Server está indisponível", kind="not_sent", method=outbound.method, process_epoch=runtime.epoch)
        try:
            runtime.outgoing.put_nowait(outbound)
        except queue.Full as error:
            raise CodexProtocolError("Fila de escrita do App Server está cheia", kind="not_sent", method=outbound.method, process_epoch=runtime.epoch) from error

    def _send_runtime(self, runtime: _Runtime, payload: dict[str, Any], timeout: float = 45) -> None:
        outbound = _Outbound(payload, payload.get("method"))
        self._enqueue(runtime, outbound)
        if not outbound.done.wait(timeout):
            with outbound.lock:
                outbound.cancelled = not outbound.started
                kind = "uncertain" if outbound.started else "not_sent"
            raise CodexProtocolError("Codex App Server excedeu o tempo de escrita", kind=kind, method=outbound.method, process_epoch=runtime.epoch, written=outbound.started)
        if outbound.error:
            raise outbound.error

    def _send(self, payload: dict[str, Any]) -> None:
        self.start()
        assert self._runtime
        self._send_runtime(self._runtime, payload)

    def request(self, method: str, params: dict[str, Any] | None = None, timeout: float = 45) -> dict[str, Any]:
        self.start()
        assert self._runtime
        return self._request_runtime(self._runtime, method, params or {}, timeout)

    def _request_runtime(self, runtime: _Runtime, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        with self.pending_lock:
            if len(self.pending) >= self.request_limit:
                raise CodexProtocolError("Limite de RPCs pendentes do App Server atingido", kind="not_sent", method=method, process_epoch=runtime.epoch)
            request_id, self.next_id = self.next_id, self.next_id + 1
            pending = _Pending(method, runtime.epoch)
            payload = {"method": method, "id": request_id}
            # account/logout é uma RPC sem parâmetros no protocolo do Codex.
            if method != "account/logout":
                payload["params"] = params
            pending.outbound = _Outbound(payload, method)
            self.pending[request_id] = pending
        try:
            self._enqueue(runtime, pending.outbound)
            try:
                message = pending.response.get(timeout=timeout)
            except queue.Empty as error:
                with pending.outbound.lock:
                    pending.outbound.cancelled = not pending.outbound.started
                    written = pending.outbound.started
                raise CodexProtocolError(f"Codex App Server excedeu o tempo de espera em {method}", kind="uncertain" if written else "not_sent", method=method, request_id=request_id, process_epoch=runtime.epoch, written=written) from error
            if isinstance(message, CodexProtocolError):
                raise message
            if message.get("error"):
                detail = message["error"] if isinstance(message["error"], dict) else {"message": str(message["error"])}
                raise CodexProtocolError(f"Codex App Server recusou {method}: {detail.get('message') or 'erro protocolar'}", kind="rejected", method=method, request_id=request_id, process_epoch=runtime.epoch, written=True, rpc_code=detail.get("code"), rpc_data=detail.get("data"))
            return message.get("result") if isinstance(message.get("result"), dict) else {}
        finally:
            with self.pending_lock:
                self.pending.pop(request_id, None)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"method": method, "params": params or {}})

    def _writer(self, runtime: _Runtime) -> None:
        while not runtime.closed.is_set() or not runtime.outgoing.empty():
            try:
                outbound = runtime.outgoing.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                runtime.writer_active = True
                serialized = json.dumps(outbound.payload, ensure_ascii=False, separators=(",", ":")) + "\n"
                if len(serialized.encode("utf-8")) > self.max_frame_bytes:
                    raise CodexProtocolError("Mensagem de saída excede o limite do App Server", kind="not_sent", method=outbound.method, process_epoch=runtime.epoch)
                with outbound.lock:
                    if outbound.cancelled:
                        continue
                    if runtime.closed.is_set() or self._runtime is not runtime or runtime.process.poll() is not None:
                        raise CodexProtocolError("Codex App Server encerrou antes da escrita", kind="not_sent", method=outbound.method, process_epoch=runtime.epoch)
                    outbound.started = True
                with self.write_lock:
                    runtime.process.stdin.write(serialized)
                    runtime.process.stdin.flush()
            except (OSError, ValueError, TypeError, CodexProtocolError) as error:
                outbound.error = error if isinstance(error, CodexProtocolError) else CodexProtocolError("Falha ao escrever no Codex App Server", kind="uncertain" if outbound.started else "not_sent", method=outbound.method, process_epoch=runtime.epoch, written=outbound.started)
                request_id = outbound.payload.get("id")
                with self.pending_lock:
                    pending = self.pending.get(request_id)
                if pending and pending.epoch == runtime.epoch:
                    self._complete_pending(pending, outbound.error)
                if outbound.started:
                    self._lose(runtime, "write_failed")
            finally:
                runtime.writer_active = False
                outbound.done.set()
                runtime.outgoing.task_done()

    @staticmethod
    def _complete_pending(pending: _Pending, message: Any) -> None:
        try:
            pending.response.put_nowait(message)
        except queue.Full:
            pass

    def _read_stdout(self, runtime: _Runtime | None = None) -> None:
        runtime = runtime or self._runtime
        if runtime is None:
            return
        try:
            while True:
                raw = runtime.process.stdout.readline(self.max_frame_bytes + 1)
                if not raw:
                    break
                with runtime.event_condition:
                    if runtime.closed.is_set():
                        break
                    runtime.frames_inflight += 1
                try:
                    keep_reading = self._consume_frame(runtime, raw)
                finally:
                    with runtime.event_condition:
                        runtime.frames_inflight -= 1
                        runtime.event_condition.notify_all()
                if not keep_reading:
                    break
        except (OSError, ValueError):
            self._lose(runtime, "read_failed")
        finally:
            self._lose(runtime, "eof")

    def _consume_frame(self, runtime: _Runtime, raw: str) -> bool:
        if len(raw.encode("utf-8")) > self.max_frame_bytes:
            self._lose(runtime, "frame_too_large")
            return False
        try:
            message = json.loads(raw)
        except ValueError:
            self._lose(runtime, "invalid_json")
            return False
        if not isinstance(message, dict):
            self._lose(runtime, "invalid_envelope")
            return False
        runtime.sequence += 1
        context = {"process_epoch": runtime.epoch, "sequence": runtime.sequence, "observed_at": time.time()}
        request_id = message.get("id")
        if "result" in message or "error" in message:
            if not _rpc_id(request_id) or ("result" in message and "error" in message):
                self._lose(runtime, "invalid_response")
                return False
            with self.pending_lock:
                target = self.pending.get(request_id)
            if target and target.epoch == runtime.epoch:
                self._complete_pending(target, message)
            else:
                self._dispatch_event(runtime, "transport/late_response", message, context)
        elif isinstance(message.get("method"), str):
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            if "id" in message and not _rpc_id(request_id):
                self._lose(runtime, "invalid_request_id")
                return False
            if _rpc_id(request_id):
                self._dispatch_request(runtime, request_id, message["method"], params, context)
            else:
                self._dispatch_event(runtime, message["method"], params, context)
        else:
            self._lose(runtime, "invalid_envelope")
            return False
        return True

    def _dispatch_event(self, runtime: _Runtime, method: str, params: dict[str, Any], context: dict[str, Any]) -> None:
        failure = None
        with runtime.event_condition:
            if runtime.closed.is_set() and not runtime.frames_inflight:
                return
            thread_id = str(params.get("threadId") or "")
            if thread_id not in runtime.routes:
                if len(runtime.routes) >= self.event_limit:
                    failure = "event_route_overflow"
                else:
                    runtime.routes[thread_id] = len(runtime.routes) % len(runtime.events)
            if not failure:
                try:
                    runtime.events[runtime.routes[thread_id]].put_nowait((method, params, context))
                    runtime.pending_events += 1
                except queue.Full:
                    failure = "event_overflow"
        if failure:
            self._lose(runtime, failure)

    def _event_worker(self, runtime: _Runtime, lane: queue.Queue) -> None:
        while True:
            with runtime.event_condition:
                if runtime.closed.is_set() and lane.empty() and not runtime.frames_inflight:
                    return
            try:
                method, params, context = lane.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                with runtime.request_lock:
                    runtime.active_events += 1
                if self.event_handler:
                    self.event_handler(method, params, context)
                elif self.notification_handler:
                    self.notification_handler(method, params)
            except Exception:
                self._lose(runtime, "event_handler_failed")
            finally:
                with runtime.request_lock:
                    runtime.active_events -= 1
                lane.task_done()
                with runtime.event_condition:
                    runtime.pending_events -= 1
                    runtime.event_condition.notify_all()

    def _dispatch_request(self, runtime: _Runtime, request_id: int | str, method: str,
                          params: dict[str, Any], context: dict[str, Any]) -> None:
        fingerprint = sha256(json.dumps([method, params], sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        with runtime.request_lock:
            if request_id in runtime.request_ids:
                if runtime.request_ids[request_id] != fingerprint:
                    self._lose(runtime, "request_identity_conflict")
                return
            if len(runtime.request_ids) >= 65536:
                self._lose(runtime, "request_identity_overflow")
                return
            runtime.request_ids[request_id] = fingerprint
            saturated = runtime.active_requests >= self.request_limit
            spawn = False
            if not saturated:
                runtime.active_requests += 1
                if runtime.request_workers_started < min(runtime.active_requests, self.request_workers):
                    runtime.request_workers_started += 1
                    spawn = True
        if saturated:
            self._queue_server_response(runtime, {"id": request_id, "error": {"code": -32001, "message": "Gateway tool capacity exceeded; retry later"}})
            return
        runtime.requests.put_nowait((request_id, method, params, context))
        if spawn:
            threading.Thread(target=self._request_worker, args=(runtime,), daemon=True).start()

    def _queue_server_response(self, runtime: _Runtime, payload: dict[str, Any]) -> None:
        try:
            self._enqueue(runtime, _Outbound(payload))
        except CodexProtocolError:
            self._lose(runtime, "server_response_overflow")

    def _request_worker(self, runtime: _Runtime) -> None:
        while not runtime.closed.is_set() or not runtime.requests.empty():
            try:
                request_id, method, params, context = runtime.requests.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if runtime.closed.is_set():
                    continue
                if self.request_event_handler:
                    result = self.request_event_handler(method, params, {**context, "rpc_id": request_id, "request_id": request_id})
                elif self.request_handler:
                    result = self.request_handler(method, params)
                else:
                    self._queue_server_response(runtime, {"id": request_id, "error": {"code": -32000, "message": "Interactive gateway requests are disabled"}})
                    continue
                self._queue_server_response(runtime, {"id": request_id, "result": result})
            except Exception:
                self._queue_server_response(runtime, {"id": request_id, "error": {"code": -32000, "message": "Gateway tool failed or was cancelled"}})
            finally:
                with runtime.request_lock:
                    runtime.active_requests -= 1
                runtime.requests.task_done()

    def _lose(self, runtime: _Runtime, reason: str) -> None:
        with self._lifecycle:
            with runtime.event_condition:
                if runtime.closed.is_set():
                    return
                runtime.closed.set()
                runtime.error = CodexProtocolError("Codex App Server perdeu o transporte", kind="transport_lost", process_epoch=runtime.epoch)
                runtime.event_condition.notify_all()
            if self._runtime is runtime:
                self.state, self.last_error = "lost", f"App Server transport: {reason}"
        with self.pending_lock:
            for request_id, pending in self.pending.items():
                if pending.epoch != runtime.epoch:
                    continue
                outbound = pending.outbound
                with outbound.lock:
                    outbound.cancelled = not outbound.started
                    written = outbound.started
                failure = CodexProtocolError(f"Codex App Server perdeu o transporte durante {pending.method}", kind="transport_lost" if written else "not_sent", method=pending.method, request_id=request_id, process_epoch=runtime.epoch, written=written)
                self._complete_pending(pending, failure)
        threading.Thread(target=self._report_loss, args=(runtime, reason), daemon=True).start()
        threading.Thread(target=self._reap, args=(runtime,), daemon=True).start()

    @staticmethod
    def _reap(runtime: _Runtime) -> None:
        try:
            runtime.process.terminate()
            runtime.process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                runtime.process.kill()
                runtime.process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        finally:
            runtime.reaped.set()
            close = getattr(runtime.process.stdin, "close", None)
            if close:
                try:
                    close()
                except (OSError, ValueError):
                    pass

    def _report_loss(self, runtime: _Runtime, reason: str) -> None:
        drained = self._wait_runtime_events(runtime, self.event_drain_timeout)
        if self.event_handler:
            try:
                self.event_handler("transport/lost", {"reason": reason if drained else "drain_incomplete", "cause": reason, "events_drained": drained}, {"process_epoch": runtime.epoch, "sequence": runtime.sequence + 1, "observed_at": time.time(), "transport_generated": True})
            except Exception:
                pass

    def _read_stderr(self, runtime: _Runtime | None = None) -> None:
        runtime = runtime or self._runtime
        if not runtime:
            return
        try:
            while True:
                raw = runtime.process.stderr.readline(self.max_frame_bytes + 1)
                if not raw:
                    break
                if raw.strip() and self._runtime is runtime:
                    self.last_error = "Codex App Server emitiu diagnóstico em stderr"
        except (OSError, ValueError):
            pass
