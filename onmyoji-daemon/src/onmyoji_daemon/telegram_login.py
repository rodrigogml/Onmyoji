"""Autenticação por código de dispositivo do Codex, exclusiva do owner na DM.

O Gateway só encaminha URL/código temporário validados. Nunca recebe tokens,
senhas ou saídas brutas do processo Codex.
"""
from __future__ import annotations

import re
import secrets
import threading
import time
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
        self._login_menus: dict[str, dict[str, Any]] = {}
        self._login_menu_lock = threading.RLock()

    def _login_pending(self) -> bool:
        with self._login_lock:
            return self._login_session is not None

    def _login_reply(self, conversation: str, text: str) -> dict[str, Any] | None:
        if not self.api:
            return None
        chat, topic = self._telegram_address(conversation)
        receipts = self._send_agent_text(chat, text, **topic)
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

    def _login_account_read(self) -> dict[str, Any] | None:
        if not self.settings.app_server_enabled:
            return None
        response = self._app_client().request("account/read", {"refreshToken": False}, timeout=25)
        account = response.get("account")
        return account if isinstance(account, dict) else {}

    @staticmethod
    def _login_account_text(account: dict[str, Any] | None, *, details: bool) -> str:
        if account is None:
            return "O login remoto exige o Codex App Server habilitado no setup do daemon."
        if not account:
            return "Nenhuma conta está conectada ao Codex."
        mode = account.get("type")
        names = {"chatgpt": "ChatGPT", "apiKey": "Chave de API", "amazonBedrock": "Amazon Bedrock"}
        result = "Autenticação: " + names.get(mode, "Outro provedor")
        if mode == "chatgpt":
            plan = account.get("planType")
            if isinstance(plan, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,24}", plan):
                result += "\nPlano: " + plan
            if details:
                email = account.get("email")
                if isinstance(email, str) and len(email) <= 254 and re.fullmatch(r"[^@\s\x00-\x1f]{1,64}@[^@\s\x00-\x1f]{1,189}", email):
                    result += "\nE-mail: " + email
                else:
                    result += "\nE-mail: não informado pelo Codex"
        elif details:
            result += "\nIdentificação da conta: não fornecida pelo Codex."
        return result

    @staticmethod
    def _login_menu_keyboard(token: str, *, page: str = "root", pending: bool = False) -> dict[str, Any]:
        def option(label: str, action: str) -> list[dict[str, str]]:
            return [{"text": label, "callback_data": f"lg:{token}:{action}"}]
        if page == "logout":
            rows = [option("Confirmar logoff", "logout-confirm"), option("‹ Voltar", "back")]
        elif page == "root":
            rows = ([option("Cancelar login em andamento", "cancel")] if pending
                    else [option("Iniciar login", "start")])
            rows += [option("Status", "status"), option("Informações da conta atual", "account")]
            if not pending:
                rows.append(option("Logoff", "logout"))
        else:
            rows = [option("‹ Voltar", "back")]
        rows.append(option("Fechar", "close"))
        return {"inline_keyboard": rows}

    def _login_menu(self, conversation: str) -> None:
        if not self.api:
            return
        generation = self._generation(conversation)
        token = secrets.token_urlsafe(9)
        pending = self._login_pending()
        text = "🔐 Conta Codex\nSelecione uma opção." + ("\nLogin em andamento." if pending else "")
        keyboard = self._login_menu_keyboard(token, pending=pending)
        started = time.monotonic()
        sent = self._ephemeral(conversation, text, 180, reply_markup=keyboard)
        self._diagnostic("menu.publish", stage="login", duration_ms=int((time.monotonic() - started) * 1000))
        message_id = sent.get("message_id") if isinstance(sent, dict) else None
        if type(message_id) is not int:
            return
        with self._login_menu_lock:
            for previous_token, previous in list(self._login_menus.items()):
                if previous["expires"] <= time.time():
                    self._login_menus.pop(previous_token, None)
            if generation == self._generation(conversation):
                self._login_menus[token] = {"conversation": conversation, "generation": generation,
                                            "message_id": message_id, "expires": time.time() + 180, "busy": False, "page": "root"}
                return
        address, _topic = self._telegram_address(conversation)
        self._delete_control_message(address, message_id)

    def _login_menu_close(self, token: str, session: dict[str, Any]) -> None:
        with self._login_menu_lock:
            if self._login_menus.get(token) is not session:
                return
            del self._login_menus[token]
        address, _topic = self._telegram_address(session["conversation"])
        self._delete_control_message(address, session["message_id"])

    def _login_menu_render(self, token: str, session: dict[str, Any], text: str, page: str) -> None:
        conversation = session["conversation"]
        address, topic = self._telegram_address(conversation)
        keyboard = self._login_menu_keyboard(token, page=page, pending=self._login_pending())
        if session["generation"] != self._generation(conversation):
            self._login_menu_close(token, session)
            return
        assert self.api
        started = time.monotonic()
        try:
            self.api.edit(address, session["message_id"], text, keyboard)
        except Exception as error:
            # Alguns tipos de tópico privado podem recusar edições; um erro HTTP
            # confirmado (4xx) permite recriar sem duplicar um envio incerto.
            if "direct_messages_topic_id" not in topic or getattr(error, "kind", None) != "not_sent":
                raise
            sent = self.api.send(address, text, reply_markup=keyboard, **topic)
            new_id = sent.get("message_id") if isinstance(sent, dict) else None
            if type(new_id) is not int:
                raise RuntimeError("Telegram não confirmou o menu de login.")
            previous = session["message_id"]
            session["message_id"] = new_id
            self._delete_control_message(address, previous)
        session["page"] = page
        self._diagnostic("menu.render", stage="login", duration_ms=int((time.monotonic() - started) * 1000))

    def _login_logout(self, conversation: str) -> str:
        if not self.settings.app_server_enabled:
            return "O login remoto exige o Codex App Server habilitado no setup do daemon."
        with self._login_lock:
            if self._login_session is not None:
                return "Existe um login em andamento. Cancele-o antes de sair da conta."
            with self.app_server_lock:
                busy = bool(self.app_turns or any(not slot.done.is_set() for slot in self._slots.values()))
                client = self.app_server
            if busy or self._has_uncertain_runtime() or self.related_work.has_pending(str(getattr(client, "process_epoch", ""))):
                return "Há execuções ou operações pendentes. Conclua ou reconcilie antes do logoff."
            try:
                self._app_client().request("account/logout", {}, timeout=25)
            except (CodexProtocolError, CodexExecutionError, ProcessRecoveryError, OSError):
                return "Não foi possível confirmar o logoff no Codex."
        self._diagnostic("codex_login.logged_out")
        return "Logoff concluído. Nenhuma conta ChatGPT permanece autenticada pelo Codex."

    def _login_menu_callback(self, conversation: str, callback: dict[str, Any], parts: list[str]) -> bool:
        if len(parts) != 3 or parts[0] != "lg":
            return False
        token, action = parts[1], parts[2]
        message = callback.get("message") or {}
        with self._login_menu_lock:
            session = self._login_menus.get(token)
            if (not session or session["conversation"] != conversation
                    or session["message_id"] != message.get("message_id")
                    or session["expires"] <= time.time()
                    or session["generation"] != self._generation(conversation)):
                session = None
            elif session["busy"]:
                return True
            else:
                pending = self._login_pending()
                allowed = ({"start", "status", "account", "logout", "close"} if not pending
                           else {"cancel", "status", "account", "close"}) if session["page"] == "root" else (
                           {"logout-confirm", "back", "close"} if session["page"] == "logout"
                           else {"back", "close"})
                if action not in allowed:
                    return True
                session["busy"] = True
        if session is None:
            self._login_reply(conversation, "Este menu de login expirou. Envie /login para abrir outro.")
            return True
        try:
            if action == "close":
                self._login_menu_close(token, session)
            elif action == "start":
                self._login_menu_close(token, session)
                self._login_start(conversation)
            elif action == "cancel":
                self._login_menu_close(token, session)
                self._login_cancel(conversation)
            elif action == "back":
                self._login_menu_render(token, session, "🔐 Conta Codex\nSelecione uma opção.", "root")
            elif action == "logout":
                self._login_menu_render(token, session, "Confirmar logoff do Codex nesta instância?\nIsso também desconecta o Codex CLI.", "logout")
            elif action == "logout-confirm":
                result = self._login_logout(conversation)
                self._login_menu_render(token, session, result, "result")
            elif action in {"status", "account"}:
                if action == "status" and self._login_pending():
                    text = "Há uma autenticação Codex em andamento. Conclua o código enviado ou cancele o login pelo menu."
                else:
                    try:
                        account = self._login_account_read()
                        text = self._login_account_text(account, details=action == "account")
                    except (CodexProtocolError, CodexExecutionError, ProcessRecoveryError, OSError):
                        text = "Não foi possível consultar a conta Codex agora."
                self._login_menu_render(token, session, text, "result")
            else:
                self._login_menu_render(token, session, "Opção inválida.", "result")
        except Exception:
            self._login_menu_close(token, session)
            self._login_reply(conversation, "Não foi possível atualizar o menu Codex. Envie /login para tentar novamente.")
            self._diagnostic("menu.failed", stage="login")
        finally:
            with self._login_menu_lock:
                if self._login_menus.get(token) is session:
                    session["busy"] = False
        return True

    def _login(self, conversation: str) -> None:
        self._login_menu(conversation)

    def _login_cancel(self, conversation: str) -> None:
        with self._login_lock:
            session = self._login_session
            if not session:
                self._login_reply(conversation, "Não há autenticação Codex em andamento.")
                return
            login_id = session.get("login_id")
            if not isinstance(login_id, str) or session["state"] in {"starting", "publishing"}:
                self._login_reply(conversation, "A autenticação ainda está iniciando. Abra /login e escolha Cancelar login após receber o código.")
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
            self._login_reply(conversation, "O cancelamento não foi confirmado. Abra /login para cancelar ou consultar o status.")
            return
        if self._login_release(session):
            self._login_reply(conversation, "Autenticação Codex cancelada. Abra /login para iniciar outra.")
            self._diagnostic("codex_login.cancelled")

    def _login_start(self, conversation: str) -> None:
        if not self.settings.app_server_enabled:
            self._login_reply(conversation, "O login remoto exige o Codex App Server habilitado no setup do daemon.")
            return
        with self._login_lock:
            if self._login_session is not None:
                self._login_reply(conversation, "Já existe uma autenticação Codex em andamento. Abra /login para consultar ou cancelar.")
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
                              + "\n\nApós confirmar, o bot avisará o resultado. Para interromper: abra /login e escolha Cancelar login.")
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
                    self._login_reply(conversation, "Não foi possível iniciar o login por código de dispositivo. Confira o Codex e a permissão de device login; abra /login novamente.")
                except Exception:
                    self._record_error("Falha ao comunicar o início da autenticação Codex.")
            self._diagnostic("codex_login.start_failed")
