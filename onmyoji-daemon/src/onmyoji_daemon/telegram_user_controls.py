"""Gateway-only Telegram registration controls (phase 2).

These handlers never call the Codex agent, enqueue work, publish MCP tools or
add a Telegram outbound channel. All identity is read from Bot API metadata;
the role registry remains the authority for registration and withdrawal.
"""
from __future__ import annotations

from dataclasses import dataclass
import secrets
import threading
import time
from typing import Any

from .telegram_user_policy import RegistrationStatus, UserRole
from .telegram_users import (
    TelegramUser, UserAccessDenied, UserNotFound, UserRegistryError,
    UserRevisionConflict,
)


PUBLIC_REGISTRATION_COMMANDS = frozenset({"/register", "/status", "/unregister"})
UNREGISTER_MENU_TTL = 300
MAX_PENDING_UNREGISTER_MENUS = 512


@dataclass(frozen=True)
class WithdrawalPrompt:
    token: str
    user_id: int
    conversation_id: str
    message_id: int
    revision: int
    generation: int
    expires_at: float


def own_dm_sender(sender: object, chat: object) -> bool:
    """Do not trust a synthetic chat ID, boolean ID or forwarded attribution."""
    if not isinstance(sender, dict) or not isinstance(chat, dict):
        return False
    user_id, chat_id = sender.get("id"), chat.get("id")
    return (chat.get("type") == "private" and type(user_id) is int and
            type(chat_id) is int and 0 < user_id <= 2**63 - 1 and
            chat_id == user_id and sender.get("is_bot") is not True)


def public_command_name(token: str, bot_username: str) -> str | None:
    """Only the exact command, or the command directed at this bot, matches."""
    if not isinstance(token, str):
        return None
    name, sep, recipient = token.partition("@")
    if name.casefold() not in PUBLIC_REGISTRATION_COMMANDS:
        return None
    if sep and (not bot_username or recipient.casefold() != bot_username.casefold()):
        return None
    return name.casefold()


class TelegramUserControls:
    """Mixin whose I/O methods are supplied by the operational Gateway.

    Initialize after Gateway has the settings/store/contacts. The pending menu
    map is process-local: after restart all previous confirmations expire
    safely; no unconfirmed withdrawal ever changes the database.
    """

    def _init_user_controls(self) -> None:
        self._withdrawal_lock = threading.RLock()
        self._withdrawal_prompts: dict[int, WithdrawalPrompt] = {}

    def _registration_reply(self, conversation_id: str, message: str, *, reply_markup: dict | None = None) -> dict | None:
        assert self.api
        address, topic = self._telegram_address(conversation_id)
        options = {"reply_markup": reply_markup} if reply_markup is not None else {}
        try:
            return self.api.send(address, message, **options, **topic)
        except Exception:
            self._record_error("telegram_user_controls_send_failed")
            return None

    def _registration_alert_owners(self, user: TelegramUser) -> None:
        """Best-effort once per transition to PENDING, without message content."""
        assert self.api
        display = user.display_name or "Usuário sem nome"
        username = f" (@{user.username})" if user.username else ""
        notice = ("Nova solicitação de cadastro Telegram.\n"
                  f"Usuário: {display}{username}\n"
                  f"ID: {user.telegram_user_id}\n"
                  "Abra /users para examinar o pedido. "
                  "A aprovação ficará disponível na Fase 3.")
        for owner_id in sorted(self.contacts.owners()):
            try:
                self.api.send(owner_id, notice)
            except Exception:
                # The persisted request does not depend on Bot API delivery.
                self._record_error("telegram_user_owner_notice_failed")

    @staticmethod
    def _registration_status_message(user: TelegramUser | None) -> str:
        if user is None:
            return "Você ainda não solicitou cadastro. Envie /register para solicitar acesso."
        if user.status == RegistrationStatus.PENDING:
            return "Seu cadastro está aguardando aprovação do proprietário."
        if user.status == RegistrationStatus.ACTIVE:
            names = {
                UserRole.CHAT_USER: "Conversar com o Shikigami",
                UserRole.NOTIFY_TARGET: "Receber notificações",
            }
            granted = ", ".join(names[role] for role in user.roles) or "nenhuma"
            return f"Cadastro ativo. Permissões: {granted}."
        if user.status == RegistrationStatus.WITHDRAWN:
            return "Seu cadastro foi retirado. Envie /register se desejar solicitar novamente."
        if user.status == RegistrationStatus.REJECTED:
            return "Seu cadastro foi rejeitado. Somente o proprietário pode revisar essa decisão."
        return "Seu cadastro está suspenso. Somente o proprietário pode reativá-lo."

    def _registration_command(self, command: str, text: str, sender: dict, conversation_id: str) -> None:
        """Run only for validated, non-edited own-DM text commands."""
        assert self.api
        user_id = sender["id"]
        if text.split(maxsplit=1)[1:] or text.split(maxsplit=1)[0].count("@") > 1:
            self._registration_reply(conversation_id, f"Use {command}, sem argumentos.")
            return
        if user_id in self.contacts.owners():
            self._registration_reply(conversation_id,
                                     "Você é Owner. Seu acesso é gerenciado pelo pareamento, não pelo cadastro de usuários.")
            return
        try:
            if command == "/register":
                result = self.users.register_from_dm(sender, conversation_id)
                messages = {
                    "PENDING": "Cadastro recebido. Aguardando aprovação do proprietário." if result.changed else
                               "Seu cadastro já está aguardando aprovação.",
                    "ACTIVE": "Seu cadastro está ativo. Consulte /status.",
                    "SUSPENDED": "Seu cadastro não está habilitado. Somente o proprietário pode reativá-lo.",
                    "REJECTED": "Seu cadastro foi rejeitado. Somente o proprietário pode revisar a decisão.",
                    "OWNER": "Você é Owner. Seu acesso não depende deste cadastro.",
                }
                self._registration_reply(conversation_id, messages.get(result.outcome, "Consulte /status."))
                if result.changed and result.user is not None:
                    self._registration_alert_owners(result.user)
                return
            if command == "/status":
                self._registration_reply(conversation_id,
                                         self._registration_status_message(self.users.self_status(user_id)))
                return
            if command == "/unregister":
                user = self.users.self_status(user_id)
                if user is None:
                    self._registration_reply(conversation_id, "Você não possui cadastro. Envie /register para solicitar acesso.")
                    return
                if user.status == RegistrationStatus.WITHDRAWN:
                    self._registration_reply(conversation_id, "Seu cadastro já foi retirado. Use /register para solicitar novamente.")
                    return
                self._registration_start_withdrawal(user, conversation_id)
                return
        except UserRegistryError:
            self._record_error("telegram_user_registry_operation_rejected")
            self._registration_reply(conversation_id,
                                     "Não foi possível processar seu cadastro. Confira os dados do Telegram e tente novamente.")

    def _registration_start_withdrawal(self, user: TelegramUser, conversation_id: str) -> None:
        assert self.api
        token = secrets.token_urlsafe(16)
        keyboard = {"inline_keyboard": [[
            {"text": "Confirmar retirada", "callback_data": f"ur:{token}:confirm"},
            {"text": "Cancelar", "callback_data": f"ur:{token}:cancel"},
        ]]}
        generation = self._generation(conversation_id)
        sent = self._registration_reply(
            conversation_id,
            "Retirar seu cadastro e revogar todas as permissões? "
            "Você poderá solicitar novamente com /register, sujeito a nova aprovação.",
            reply_markup=keyboard,
        )
        message_id = sent.get("message_id") if isinstance(sent, dict) else None
        if type(message_id) is not int or message_id <= 0:
            self._record_error("telegram_user_withdrawal_receipt_invalid")
            return
        session = WithdrawalPrompt(token, user.telegram_user_id, conversation_id,
                                   message_id, user.revision, generation,
                                   time.monotonic() + UNREGISTER_MENU_TTL)
        with self._withdrawal_lock:
            expired = [uid for uid, prompt in self._withdrawal_prompts.items()
                       if prompt.expires_at <= time.monotonic()]
            for uid in expired:
                self._withdrawal_prompts.pop(uid, None)
            if len(self._withdrawal_prompts) >= MAX_PENDING_UNREGISTER_MENUS:
                self._withdrawal_prompts.pop(next(iter(self._withdrawal_prompts)))
            self._withdrawal_prompts[user.telegram_user_id] = session

    def _registration_ack(self, callback: dict) -> None:
        assert self.api
        callback_id = callback.get("id")
        if not isinstance(callback_id, str) or not callback_id:
            return

        def acknowledge() -> None:
            try:
                answer = getattr(self.api, "answer_callback", None)
                if callable(answer):
                    answer(callback_id)
                else:
                    self.api.call("answerCallbackQuery", {"callback_query_id": callback_id})
            except Exception:
                self._record_error("telegram_user_callback_ack_failed")

        # Avoid blocking message processing on the Telegram Bot API.
        if callable(getattr(self.api, "answer_callback", None)):
            threading.Thread(target=acknowledge, daemon=True).start()
        else:
            acknowledge()

    def _registration_callback(self, callback: dict) -> bool:
        """Consume only ur: callbacks; never route them to agent/Owner menus."""
        data = callback.get("data")
        if not isinstance(data, str) or not data.startswith("ur:"):
            return False
        self._registration_ack(callback)
        pieces = data.split(":")
        sender = callback.get("from")
        message = callback.get("message")
        if not isinstance(message, dict) or not own_dm_sender(sender, message.get("chat")):
            return True
        assert isinstance(sender, dict)
        assert self.api
        user_id = sender["id"]
        if len(pieces) != 3 or pieces[2] not in {"confirm", "cancel"} or len(pieces[1]) > 64:
            return True
        msg_id = message.get("message_id")
        if type(msg_id) is not int or msg_id <= 0:
            return True
        with self._withdrawal_lock:
            prompt = self._withdrawal_prompts.get(user_id)
            valid = bool(prompt and secrets.compare_digest(prompt.token, pieces[1]) and
                         prompt.message_id == msg_id and
                         prompt.expires_at > time.monotonic())
            if valid:
                # One-shot claim before accessing SQLite, even for cancellation.
                self._withdrawal_prompts.pop(user_id, None)
            elif prompt and prompt.expires_at <= time.monotonic():
                self._withdrawal_prompts.pop(user_id, None)
        if not valid or prompt is None:
            self._registration_reply(str(user_id), "Confirmação inválida, expirada ou já utilizada. Envie /unregister novamente.")
            return True
        expected_chat_id, _ = self._telegram_address(prompt.conversation_id)
        if expected_chat_id != user_id or self._generation(prompt.conversation_id) != prompt.generation:
            self._registration_reply(prompt.conversation_id, "Esta confirmação expirou. Envie /unregister novamente.")
            return True
        if "direct_messages_topic" in message or "message_thread_id" in message:
            # Telegram may omit private topic metadata on a callback; if present,
            # it must match the original menu's canonical conversation.
            try:
                if self._conversation_id(message["chat"], message) != prompt.conversation_id:
                    return True
            except (ValueError, KeyError, TypeError):
                return True
        if pieces[2] == "cancel":
            self._registration_reply(prompt.conversation_id, "Retirada cancelada. Nenhuma permissão foi alterada.")
            return True
        try:
            mutation = self.users.withdraw(user_id, prompt.revision)
        except UserRevisionConflict:
            self._registration_reply(prompt.conversation_id,
                                     "Seu cadastro mudou desde a abertura do menu. Consulte /status e tente novamente.")
            return True
        except (UserNotFound, UserAccessDenied, UserRegistryError):
            self._registration_reply(prompt.conversation_id,
                                     "Não foi possível retirar este cadastro. Consulte /status.")
            return True
        if mutation.changed:
            self._registration_reply(prompt.conversation_id,
                                     "Cadastro retirado e permissões revogadas. "
                                     "Para solicitar novamente, use /register.")
        else:
            self._registration_reply(prompt.conversation_id, "Seu cadastro já havia sido retirado.")
        return True
