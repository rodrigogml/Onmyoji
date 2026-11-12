"""Autenticação por código de dispositivo do Codex, exclusiva do owner na DM.

O Gateway só encaminha URL/código temporário validados. Nunca recebe tokens,
senhas ou saídas brutas do processo Codex.
"""
from __future__ import annotations

import re
import threading
from typing import Any
from urllib.parse import urlsplit

from .codex_app_server import CodexProtocolError
from .codex_execution import CodexExecutionError
from .telegram_process import ProcessRecoveryError


class TelegramLogin:
    def _initialize_login(self) -> None:
        self._login_lock = threading.RLock()
        self._login_session: dict[str, Any] | None = None
        self._login_early_completion: tuple[dict[str, Any], dict[str, Any] | None] | None = None

    def _login_pending(self) -> bool:
        with self._login_lock:
            return self._login_session is not None

    def _login_reply(self, conversation: str, text: str) -> dict[str, Any] | None:
        if not self.api:
            return None
        chat, topic = self._telegram_address(conversation)
        receipts = self._send_agent_text(chat, text, protect_content=True, **topic)
        return receipts[-1] if receipts else None

    def _login_release(self, session: dict[str, Any]) -> bool:
        with self._login_lock:
            if self._login_session is not session:
                return False
            self._login_session = None
            self._login_early_completion = None
        with self.workers_changed:
            self.workers_changed.notify_all()
        return True

    def _login_event(self, method: str, params: dict[str, Any],
                     event: dict[str, Any] | None = None) -> None:
        if method not in {"account/login/completed", "transport/lost"}:
            return
        with self._login_lock:
            session = self._login_session
            if session is None:
                return
            epoch = str((event or {}).get("process_epoch") or "")
            if session.get("epoch") and epoch != session["epoch"]:
                return
            if method == "transport/lost":
                if not session.get("epoch"):
                    return
                response = "A autenticação Codex foi interrompida porque o App Server encerrou. Use /login novamente."
            else:
                login_id = params.get("loginId")
                if not isinstance(login_id, str) or not login_id:
                    return
                if session["state"] in {"starting", "publishing"}:
                    self._login_early_completion = (dict(params), dict(event) if event else None)
                    return
                if login_id != session.get("login_id"):
                    return
                response = ("✅ Autenticação Codex concluída. A conta ChatGPT está conectada."
                            if params.get("success") is True
                            else "Não foi possível concluir a autenticação Codex. Use /login para tentar novamente.")
            conversation = session["conversation"]
        if self._login_release(session):
            self._diagnostic("codex_login.completed", outcome="lost" if method == "transport/lost" else "success" if params.get("success") is True else "failed")
            try:
                self._login_reply(conversation, response)
            except Exception:
                self._record_error("Falha no envio do resultado da autenticação Codex.")

    def _login_status(self, conversation: str) -> None:
        with self._login_lock:
            session = self._login_session
            if session:
                self._login_reply(conversation, "Há uma autenticação Codex em andamento. Conclua o código enviado ou use /login cancelar.")
                return
        if not self.settings.app_server_enabled:
            self._login_reply(conversation, "O login remoto exige o Codex App Server habilitado no setup do daemon.")
            return
        try:
            response = self._app_client().request("account/read", {"refreshToken": False}, timeout=25)
        except (CodexProtocolError, CodexExecutionError, ProcessRecoveryError, OSError):
            self._login_reply(conversation, "Não foi possível consultar a autenticação Codex agora.")
            return
        account = response.get("account")
        if not isinstance(account, dict):
            self._login_reply(conversation, "Codex sem conta ChatGPT conectada, ou com autenticação indisponível. Use /login para autenticar.")
            return
        mode = account.get("type")
        if mode == "chatgpt":
            plan = account.get("planType")
            if not isinstance(plan, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", plan):
                plan = None
            self._login_reply(conversation, "Codex autenticado via ChatGPT" + (f" (plano {plan})" if plan else "") + ". Use /login para trocar a conta.")
        elif mode == "apiKey":
            self._login_reply(conversation, "Codex autenticado por chave de API. Use /login para trocar para ChatGPT.")
        elif mode == "amazonBedrock":
            self._login_reply(conversation, "Codex utiliza Amazon Bedrock. Use /login para autenticação ChatGPT, se a configuração permitir.")
        else:
            self._login_reply(conversation, "O Codex utiliza outro modo de autenticação. Use /login para iniciar autenticação ChatGPT.")

    def _login_cancel(self, conversation: str) -> None:
        with self._login_lock:
            session = self._login_session
            if not session:
                self._login_reply(conversation, "Não há autenticação Codex em andamento.")
                return
            login_id = session.get("login_id")
            if not isinstance(login_id, str) or session["state"] in {"starting", "publishing"}:
                self._login_reply(conversation, "A autenticação ainda está iniciando. Use /login cancelar após receber o código.")
                return
            if session["state"] == "cancelling":
                self._login_reply(conversation, "O cancelamento da autenticação já foi solicitado.")
                return
            session["state"] = "cancelling"
        try:
            client = self._app_client()
            client.request("account/login/cancel", {"loginId": login_id}, timeout=25)
        except (CodexProtocolError, CodexExecutionError, ProcessRecoveryError, OSError):
            with self._login_lock:
                if self._login_session is session and session["state"] == "cancelling":
                    session["state"] = "pending"
            self._login_reply(conversation, "O cancelamento não foi confirmado. Tente /login cancelar novamente ou consulte /login status.")
            return
        if self._login_release(session):
            self._login_reply(conversation, "Autenticação Codex cancelada. Use /login para iniciar outra.")
            self._diagnostic("codex_login.cancelled")

    def _login(self, conversation: str, argument: str) -> None:
        action = argument.strip().casefold()
        if action == "status":
            self._login_status(conversation)
            return
        if action == "cancelar":
            self._login_cancel(conversation)
            return
        if action:
            self._login_reply(conversation, "Use /login, /login status ou /login cancelar.")
            return
        if not self.settings.app_server_enabled:
            self._login_reply(conversation, "O login remoto exige o Codex App Server habilitado no setup do daemon.")
            return
        with self._login_lock:
            if self._login_session is not None:
                self._login_reply(conversation, "Já existe uma autenticação Codex em andamento. Use /login status ou /login cancelar.")
                return
            with self.app_server_lock:
                busy = bool(self.app_turns or any(not slot.done.is_set() for slot in self._slots.values()))
                client = self.app_server
            if busy or self._has_uncertain_runtime() or self.related_work.has_pending(str(getattr(client, "process_epoch", ""))):
                self._login_reply(conversation, "Há execuções ou operações pendentes no Codex. Conclua ou reconcilie essas operações antes de trocar a conta.")
                return
            session: dict[str, Any] = {"state": "starting", "conversation": conversation, "epoch": None, "login_id": None}
            self._login_session = session
        try:
            client = self._app_client()
            with self._login_lock:
                if self._login_session is not session:
                    return
                session["epoch"] = str(getattr(client, "process_epoch", ""))
            response = client.request("account/login/start", {"type": "chatgptDeviceCode"}, timeout=30)
            login_id, url, code = response.get("loginId"), response.get("verificationUrl"), response.get("userCode")
            parsed = urlsplit(url) if isinstance(url, str) else None
            if (response.get("type") != "chatgptDeviceCode"
                    or not isinstance(login_id, str) or not login_id
                    or not parsed or parsed.scheme != "https" or parsed.hostname != "auth.openai.com"
                    or parsed.username is not None or parsed.password is not None
                    or not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9-]{4,32}", code)):
                raise ValueError("Resposta de autenticação inválida")
            with self._login_lock:
                if self._login_session is not session:
                    return
                session["login_id"] = login_id
                session["state"] = "publishing"
            self._login_reply(conversation, "🔐 Login Codex CLI\n\nAbra o endereço no navegador:\n"
                              + url + "\n\nCódigo: " + code
                              + "\n\nApós confirmar, o bot avisará o resultado. Para interromper: /login cancelar.")
            with self._login_lock:
                if self._login_session is not session:
                    return
                session["state"] = "pending"
                early = self._login_early_completion
                self._login_early_completion = None
            if early:
                self._login_event("account/login/completed", *early)
            self._diagnostic("codex_login.started")
        except Exception:
            # Falhas no Telegram também liberam o bloqueio global. Se o App
            # Server já iniciou o desafio, tentamos cancelá-lo sem reenviar o código.
            login_id = session.get("login_id")
            if isinstance(login_id, str) and login_id:
                try:
                    client.request("account/login/cancel", {"loginId": login_id}, timeout=10)
                except Exception:
                    pass
            if self._login_release(session):
                try:
                    self._login_reply(conversation, "Não foi possível iniciar o login por código de dispositivo. Confira o Codex e a permissão de device login; tente /login novamente.")
                except Exception:
                    self._record_error("Falha ao comunicar o início da autenticação Codex.")
            self._diagnostic("codex_login.start_failed")
