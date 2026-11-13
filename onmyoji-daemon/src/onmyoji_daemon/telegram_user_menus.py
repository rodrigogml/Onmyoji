"""Owner-only Telegram inline administration for registered users (phase 3).

This mixin is intentionally only an administrative surface. Mutations are always
executed by TelegramUserRegistry against current SQLite revisions; it neither
authorizes agent ingress nor creates notification destinations. HTTP calls are
never made while holding menu locks or the registry's SQLite mutex.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import secrets
import threading
import time
from typing import Any

from .telegram_user_controls import own_dm_sender
from .telegram_user_policy import RegistrationStatus, UserRole
from .telegram_users import (
    TelegramUser, UserAccessDenied, UserNotFound, UserRegistryError,
    UserRevisionConflict, UserTransitionError,
)


MENU_TTL_SECONDS = 15 * 60
MENU_PAGE_SIZE = 8
MENU_MAX_QUERY = 100
_MENU_STATES = {
    "p": ("Pendentes", RegistrationStatus.PENDING),
    "a": ("Ativos", RegistrationStatus.ACTIVE),
    "s": ("Suspensos", RegistrationStatus.SUSPENDED),
    "r": ("Rejeitados", RegistrationStatus.REJECTED),
    "t": ("Todos", None),
}
_ROLE_LABELS = {
    UserRole.CHAT_USER: "Conversar com o Shikigami",
    UserRole.NOTIFY_TARGET: "Receber notificações",
}


@dataclass(frozen=True)
class UsersMenu:
    owner_id: int
    conversation_id: str
    generation: int
    token: str
    message_id: int
    expires_at: float
    view: str = "home"
    category: str = "p"
    query: str = ""
    cursor: str | None = None
    history: tuple[str | None, ...] = ()
    item_ids: tuple[int, ...] = ()
    next_cursor: str | None = None
    target_id: int | None = None
    target_revision: int | None = None
    draft_roles: frozenset[UserRole] = frozenset()
    destructive_action: RegistrationStatus | None = None
    note: str = ""


def _button(text: str, token: str, action: str) -> dict[str, str]:
    data = f"usr:{token}:{action}"
    if len(data.encode("utf-8")) > 64:
        raise ValueError("Telegram callback payload exceeds 64 bytes.")
    return {"text": text, "callback_data": data}


def _timestamp(value: float) -> str:
    return time.strftime("%d/%m/%Y %H:%M UTC", time.gmtime(value))


def _display_name(user: TelegramUser, *, short: bool = False) -> str:
    label = user.display_name or ("@" + user.username if user.username else "Usuário sem nome")
    return label[:30] if short else label[:140]


class TelegramUserMenus:
    """Gateway mixin: one active token-bound menu per Owner at a time."""

    def _init_users_menus(self) -> None:
        self._users_menu_lock = threading.RLock()
        self._users_menu_sessions: dict[int, UsersMenu] = {}
        self._users_menu_epoch: dict[int, int] = {}

    def _users_menu_start(self, conversation_id: str, sender: dict[str, Any], text: str) -> None:
        """Only Gateway._update calls this after verifying the real own-DM."""
        owner_id = sender.get("id")
        if type(owner_id) is not int or owner_id not in self.contacts.owners():
            return
        elements = text.split(maxsplit=1)
        command, raw_query = elements[0], elements[1] if len(elements) > 1 else ""
        expected = getattr(self, "bot_username", "") or ""
        base, sep, addressed = command.partition("@")
        if base.casefold() != "/users" or (sep and (not expected or addressed.casefold() != expected.casefold())):
            return
        query = raw_query.strip()
        if query.startswith("@") and len(query) > 1:
            query = query[1:]  # Usernames in SQLite do not include Telegram @.
        if len(query) > MENU_MAX_QUERY or "\n" in query or "\r" in query:
            self._registration_reply(conversation_id, "Busca muito longa ou inválida. Use /users seguido de até 100 caracteres.")
            return
        session = UsersMenu(owner_id=owner_id, conversation_id=conversation_id,
                            generation=self._generation(conversation_id),
                            token=secrets.token_urlsafe(16), message_id=0,
                            expires_at=time.monotonic() + MENU_TTL_SECONDS,
                            view="list" if query else "home", category="t" if query else "p", query=query)
        with self._users_menu_lock:
            self._users_menu_sessions.pop(owner_id, None)
            epoch = self._users_menu_epoch.get(owner_id, 0) + 1
            self._users_menu_epoch[owner_id] = epoch
        self._users_menu_publish(session, epoch, initial=True)

    def _users_menu_render(self, session: UsersMenu) -> tuple[UsersMenu, str, dict[str, Any]]:
        token = session.token
        keyboard: list[list[dict[str, str]]] = []
        note = (session.note + "\n\n") if session.note else ""
        if session.view == "home":
            counts = self.users.status_counts(session.owner_id)
            text = (note + "👥 Administração de usuários\n"
                    "Selecione uma categoria. Para buscar nome, @usuário ou ID, use /users texto.\n\n"
                    f"Pendentes: {counts[RegistrationStatus.PENDING]}  ·  "
                    f"Ativos: {counts[RegistrationStatus.ACTIVE]}\n"
                    f"Suspensos: {counts[RegistrationStatus.SUSPENDED]}  ·  "
                    f"Rejeitados: {counts[RegistrationStatus.REJECTED]}\n"
                    f"Retirados: {counts[RegistrationStatus.WITHDRAWN]}")
            keyboard = [
                [_button(f"Pendentes ({counts[RegistrationStatus.PENDING]})", token, "p"),
                 _button(f"Ativos ({counts[RegistrationStatus.ACTIVE]})", token, "a")],
                [_button(f"Suspensos ({counts[RegistrationStatus.SUSPENDED]})", token, "s"),
                 _button(f"Rejeitados ({counts[RegistrationStatus.REJECTED]})", token, "r")],
                [_button("Todos", token, "t"), _button("Fechar", token, "x")],
            ]
            return replace(session, item_ids=(), next_cursor=None), text, {"inline_keyboard": keyboard}
        if session.view == "list":
            label, status = _MENU_STATES[session.category]
            page = self.users.list_users(session.owner_id, status=status,
                                         cursor=session.cursor, query=session.query, limit=MENU_PAGE_SIZE)
            users = page.users
            page_no = len(session.history) + 1
            heading = f"👥 {label} · página {page_no}"
            if session.query:
                heading += f" · busca: {session.query[:60]}"
            text = note + heading + "\nSelecione um usuário para administrar suas permissões."
            if not users:
                text += "\n\nNenhum cadastro nesta página."
            else:
                text += "\n\n" + "\n".join(
                    f"{idx + 1}. {_display_name(user, short=True)} · {user.telegram_user_id} · {user.status.value}"
                    for idx, user in enumerate(users)
                )
            for idx, user in enumerate(users):
                keyboard.append([_button(f"{idx + 1}. {_display_name(user, short=True)} · {user.telegram_user_id}", token, f"d{idx}")])
            navigation = []
            if session.history:
                navigation.append(_button("◀ Anterior", token, "b"))
            if page.next_cursor:
                navigation.append(_button("Próxima ▶", token, "n"))
            if navigation:
                keyboard.append(navigation)
            keyboard.append([_button("Início", token, "h"), _button("Fechar", token, "x")])
            return replace(session, item_ids=tuple(user.telegram_user_id for user in users),
                           next_cursor=page.next_cursor), text, {"inline_keyboard": keyboard}
        if session.view in {"detail", "confirm", "stale"}:
            user = self.users.self_status(session.target_id) if session.target_id is not None else None
            if user is None:
                text = note + "Cadastro indisponível. Ele pode ter sido removido ou se tornado Owner."
                keyboard = [[_button("Voltar", token, "b"), _button("Fechar", token, "x")]]
                return replace(session, view="stale"), text, {"inline_keyboard": keyboard}
            if session.view == "stale" or user.revision != session.target_revision:
                text = (note + "⚠ O cadastro foi alterado desde que este menu foi aberto. "
                        "Atualize antes de tomar uma decisão.")
                keyboard = [[_button("Atualizar cadastro", token, "f")],
                            [_button("Lista", token, "b"), _button("Fechar", token, "x")]]
                return replace(session, view="stale"), text, {"inline_keyboard": keyboard}
            granted = ", ".join(_ROLE_LABELS[r] for r in user.roles) or "nenhum"
            chosen = ", ".join(_ROLE_LABELS[r] for r in sorted(session.draft_roles, key=lambda role: role.value)) or "nenhum"
            text = (note + f"👤 {_display_name(user)}\n"
                    f"ID: {user.telegram_user_id}\n"
                    f"Username: {'@' + user.username if user.username else 'não informado'}\n"
                    f"Status: {user.status.value}\n"
                    f"Solicitado: {_timestamp(user.requested_at)}\n"
                    f"DM registrada: {user.dm_conversation_id}\n"
                    f"Revisão: {user.revision}\n"
                    f"Roles atuais: {granted}\n"
                    f"Seleção: {chosen}")
            if user.status == RegistrationStatus.WITHDRAWN:
                text += "\n\nCadastro retirado. A pessoa deve enviar /register novamente."
                keyboard = [[_button("Voltar", token, "b"), _button("Fechar", token, "x")]]
            elif session.view == "confirm":
                verb = "SUSPENDER" if session.destructive_action == RegistrationStatus.SUSPENDED else "REJEITAR"
                text += f"\n\n⚠ Confirma {verb} este usuário? Todas as permissões serão revogadas."
                keyboard = [[_button("Confirmar", token, "k"), _button("Cancelar", token, "z")]]
            else:
                keyboard = [
                    [_button(("☑ " if UserRole.CHAT_USER in session.draft_roles else "☐ ") +
                             "Conversar com o Shikigami", token, "c")],
                    [_button(("☑ " if UserRole.NOTIFY_TARGET in session.draft_roles else "☐ ") +
                             "Receber notificações", token, "o")],
                    [_button("Salvar roles e ativar/reativar", token, "v")],
                    [_button("Suspender", token, "s"), _button("Rejeitar", token, "r")],
                    [_button("Lista", token, "b"), _button("Fechar", token, "x")],
                ]
            return session, text, {"inline_keyboard": keyboard}
        raise ValueError("Invalid admin menu state.")

    def _users_menu_publish(self, session: UsersMenu, epoch: int, *, initial: bool = False) -> None:
        """Render and send/edit outside locks; store only after a confirmed receipt."""
        if session.owner_id not in self.contacts.owners():
            return
        try:
            session, text, markup = self._users_menu_render(session)
        except (UserRegistryError, ValueError, OSError):
            self._record_error("telegram_user_menu_render_failed")
            self._registration_reply(session.conversation_id, "Não foi possível carregar os cadastros. Tente /users novamente.")
            return
        with self._users_menu_lock:
            if self._users_menu_epoch.get(session.owner_id) != epoch:
                return
        assert self.api
        message_id: int | None = None
        try:
            if not initial and type(session.message_id) is int and session.message_id > 0:
                answer = self.api.call("editMessageText", {
                    "chat_id": session.owner_id, "message_id": session.message_id,
                    "text": text, "reply_markup": markup,
                })
                if answer is not False and answer is not None:
                    message_id = session.message_id
        except Exception:
            # Telegram may reject editing an old menu: use a fresh message.
            message_id = None
        if message_id is None:
            sent = self._registration_reply(session.conversation_id, text, reply_markup=markup)
            candidate = sent.get("message_id") if isinstance(sent, dict) else None
            if type(candidate) is not int or candidate <= 0:
                self._record_error("telegram_user_menu_receipt_invalid")
                return
            message_id = candidate
        updated = replace(session, message_id=message_id)
        with self._users_menu_lock:
            if self._users_menu_epoch.get(session.owner_id) == epoch and session.owner_id in self.contacts.owners():
                self._users_menu_sessions[session.owner_id] = updated

    def _users_notify(self, user: TelegramUser, *, action: str) -> None:
        """A successful SQLite mutation is never rolled back for Bot API errors."""
        assert self.api
        descriptions = {
            "roles": "Seu cadastro Telegram foi aprovado/atualizado. Consulte /status para ver suas permissões.",
            "suspend": "Seu cadastro Telegram foi suspenso e suas permissões revogadas. Consulte /status.",
            "reject": "Seu cadastro Telegram foi rejeitado e suas permissões revogadas. Consulte /status.",
        }
        try:
            chat_id, topic = self._telegram_address(user.dm_conversation_id)
            self.api.send(chat_id, descriptions[action], **topic)
        except Exception:
            self._record_error("telegram_user_menu_notice_failed")

    def _users_close_menu(self, session: UsersMenu) -> None:
        assert self.api
        try:
            self.api.call("editMessageReplyMarkup", {
                "chat_id": session.owner_id, "message_id": session.message_id,
                "reply_markup": {"inline_keyboard": []},
            })
        except Exception:
            self._record_error("telegram_user_menu_close_failed")

    def _users_menu_callback(self, callback: dict[str, Any]) -> bool:
        data = callback.get("data")
        if not isinstance(data, str) or not data.startswith("usr:"):
            return False
        # Always acknowledge the callback, even when it is invalid/unauthorized.
        self._registration_ack(callback)
        parts = data.split(":")
        sender, message = callback.get("from"), callback.get("message")
        if len(parts) != 3 or len(data.encode("utf-8")) > 64:
            return True
        if not isinstance(sender, dict) or not isinstance(message, dict) or not own_dm_sender(sender, message.get("chat")):
            return True
        owner_id = sender["id"]
        if owner_id not in self.contacts.owners():
            return True
        message_id = message.get("message_id")
        if type(message_id) is not int or message_id <= 0:
            return True
        with self._users_menu_lock:
            session = self._users_menu_sessions.get(owner_id)
            if session is None or session.expires_at <= time.monotonic():
                self._users_menu_sessions.pop(owner_id, None)
                return True
            if (session.message_id != message_id or
                    not secrets.compare_digest(session.token, parts[1]) or
                    self._generation(session.conversation_id) != session.generation):
                return True
            if self._telegram_address(session.conversation_id)[0] != owner_id:
                return True
            # Telegram sometimes omits private topic metadata on callbacks.
            # Whenever it is present, it must agree with the original menu.
            if "message_thread_id" in message or "direct_messages_topic" in message:
                try:
                    if self._conversation_id(message["chat"], message) != session.conversation_id:
                        return True
                except (KeyError, ValueError, TypeError):
                    return True
            self._users_menu_sessions.pop(owner_id, None)
            epoch = self._users_menu_epoch.get(owner_id, 0) + 1
            self._users_menu_epoch[owner_id] = epoch
        action = parts[2]
        if action == "x":
            self._users_close_menu(session)
            return True
        note = ""
        changed_user: TelegramUser | None = None
        notify_action = ""
        try:
            if session.view == "home":
                if action not in _MENU_STATES:
                    return True
                session = replace(session, view="list", category=action, cursor=None, history=())
            elif session.view == "list":
                if action.startswith("d") and len(action) == 2 and action[1] in "01234567":
                    index = int(action[1])
                    if index >= len(session.item_ids):
                        return True
                    user = self.users.self_status(session.item_ids[index])
                    if user is None:
                        return True
                    session = replace(session, view="detail", target_id=user.telegram_user_id,
                                      target_revision=user.revision,
                                      draft_roles=frozenset(user.roles), destructive_action=None)
                elif action == "n" and session.next_cursor:
                    session = replace(session, cursor=session.next_cursor,
                                      history=(*session.history, session.cursor))
                elif action == "b" and session.history:
                    session = replace(session, cursor=session.history[-1], history=session.history[:-1])
                elif action == "h":
                    session = replace(session, view="home", query="", history=(), cursor=None)
                else:
                    return True
            elif session.view == "stale":
                if action == "f":
                    user = self.users.self_status(session.target_id)
                    if user is None:
                        session = replace(session, view="list", target_id=None, target_revision=None)
                    else:
                        session = replace(session, view="detail", target_revision=user.revision,
                                          draft_roles=frozenset(user.roles), destructive_action=None)
                elif action == "b":
                    session = replace(session, view="list", target_id=None, target_revision=None)
                else:
                    return True
            elif session.view == "confirm":
                if action == "z":
                    session = replace(session, view="detail", destructive_action=None)
                elif action == "k" and session.target_id is not None and session.target_revision is not None:
                    status = session.destructive_action
                    if status not in {RegistrationStatus.SUSPENDED, RegistrationStatus.REJECTED}:
                        return True
                    result = self.users.set_status(owner_id, session.target_id, status, session.target_revision)
                    changed_user = result.user if result.changed else None
                    notify_action = "suspend" if status == RegistrationStatus.SUSPENDED else "reject"
                    session = replace(session, view="detail", target_revision=result.user.revision,
                                      draft_roles=frozenset(result.user.roles), destructive_action=None)
                    note = "Status atualizado." if result.changed else "O cadastro já estava neste status."
                else:
                    return True
            elif session.view == "detail":
                if action == "b":
                    session = replace(session, view="list", target_id=None, target_revision=None)
                elif action in {"c", "o"}:
                    role = UserRole.CHAT_USER if action == "c" else UserRole.NOTIFY_TARGET
                    updated_roles = set(session.draft_roles)
                    if role in updated_roles:
                        updated_roles.remove(role)
                    else:
                        updated_roles.add(role)
                    session = replace(session, draft_roles=frozenset(updated_roles))
                elif action == "v":
                    if not session.draft_roles:
                        note = "Selecione pelo menos uma permissão; para remover todas use Suspender."
                    elif session.target_id is None or session.target_revision is None:
                        return True
                    else:
                        result = self.users.set_roles(owner_id, session.target_id,
                                                      session.draft_roles, session.target_revision)
                        changed_user = result.user if result.changed else None
                        notify_action = "roles"
                        session = replace(session, target_revision=result.user.revision,
                                          draft_roles=frozenset(result.user.roles))
                        note = "Permissões salvas." if result.changed else "Permissões já estavam atualizadas."
                elif action in {"s", "r"}:
                    session = replace(session, view="confirm",
                                      destructive_action=(RegistrationStatus.SUSPENDED if action == "s" else
                                                          RegistrationStatus.REJECTED))
                else:
                    return True
            else:
                return True
        except UserRevisionConflict:
            session = replace(session, view="stale", destructive_action=None)
            note = "O cadastro foi alterado por outra ação. Atualize antes de continuar."
        except (UserNotFound, UserAccessDenied, UserTransitionError, UserRegistryError):
            session = replace(session, view="stale", destructive_action=None)
            note = "Não foi possível aplicar a operação. Atualize o cadastro ou solicite novo registro."
        if changed_user is not None:
            self._users_notify(changed_user, action=notify_action)
        refreshed = replace(session, token=secrets.token_urlsafe(16),
                            expires_at=time.monotonic() + MENU_TTL_SECONDS, note=note)
        self._users_menu_publish(refreshed, epoch)
        return True
