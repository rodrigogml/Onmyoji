"""Conexões por chamada, sempre autenticadas após TLS validado."""
from __future__ import annotations

from contextlib import contextmanager
import base64
import poplib
import smtplib
import ssl

from ..credentials import KeePassCredentialResolver
from .contracts import EmailError, require
from .oauth import EmailOAuth


class BoundedPOP:
    """Limita respostas multilinha antes de montar a mensagem em memória."""
    max_response_bytes = 52428800

    def _getlongresp(self):
        response = self._getresp(); lines = []; total = 0
        while True:
            line, size = self._getline()
            if line == b".": break
            total += size
            if total > self.max_response_bytes:
                self.close()
                raise EmailError("message_too_large", "Resposta POP3 excede o limite configurado.")
            lines.append(line[1:] if line.startswith(b"..") else line)
        return response, lines, total


class SecurePOP(BoundedPOP, poplib.POP3_SSL): pass
class StartTLSPOP(BoundedPOP, poplib.POP3): pass


class Connections:
    def __init__(self, root, account, limits):
        self.root, self.account, self.limits = root, account, limits

    def secret(self, kind):
        auth = self.account[kind]["auth"]
        if auth["mode"] == "oauth2": return EmailOAuth(self.root, self.account, kind, self.limits["timeout_seconds"]).access_token()
        return KeePassCredentialResolver(self.root).resolve(auth["credential_profile"], auth["credential_entry_path"], auth.get("password_field", "password"))

    @contextmanager
    def open(self, kind):
        endpoint = self.account.get(kind)
        require(bool(endpoint), "protocol_unavailable", "Conexão não configurada nesta conta.")
        protocol = endpoint["protocol"]; connection = None
        try:
            context = ssl.create_default_context(); timeout = self.limits["timeout_seconds"]
            if protocol == "imap":
                try: from imapclient import IMAPClient
                except ImportError as error: raise EmailError("runtime_not_ready", "Prepare o runtime MCP para instalar IMAPClient.") from error
                connection = IMAPClient(endpoint["host"], port=endpoint["port"], ssl=endpoint["security"] == "tls", ssl_context=context, timeout=timeout)
                if endpoint["security"] == "starttls": connection.starttls(context)
                if endpoint["auth"]["mode"] == "oauth2": connection.oauth2_login(endpoint["username"], self.secret(kind))
                else: connection.login(endpoint["username"], self.secret(kind))
            elif protocol == "pop3":
                factory = SecurePOP if endpoint["security"] == "tls" else StartTLSPOP
                kwargs = {"context": context} if endpoint["security"] == "tls" else {}
                connection = factory(endpoint["host"], endpoint["port"], timeout=timeout, **kwargs)
                connection.max_response_bytes = self.limits["max_message_bytes"]
                if endpoint["security"] == "starttls": connection.stls(context)
                if endpoint["auth"]["mode"] == "oauth2":
                    connection._putcmd("AUTH XOAUTH2")
                    require(connection._getresp().startswith(b"+"), "authentication_failed", "POP3 não iniciou OAuth.")
                    payload = f"user={endpoint['username']}\x01auth=Bearer {self.secret(kind)}\x01\x01".encode()
                    connection._putline(base64.b64encode(payload)); connection._getresp()
                else:
                    connection.user(endpoint["username"]); connection.pass_(self.secret(kind))
            else:
                factory = smtplib.SMTP_SSL if endpoint["security"] == "tls" else smtplib.SMTP
                kwargs = {"context": context} if endpoint["security"] == "tls" else {}
                connection = factory(endpoint["host"], endpoint["port"], timeout=timeout, **kwargs)
                connection.ehlo()
                if endpoint["security"] == "starttls": connection.starttls(context=context); connection.ehlo()
                if endpoint["auth"]["mode"] == "oauth2":
                    token = self.secret(kind)
                    connection.auth("XOAUTH2", lambda challenge=None: f"user={endpoint['username']}\x01auth=Bearer {token}\x01\x01" if challenge is None else "")
                else: connection.login(endpoint["username"], self.secret(kind))
            yield connection
        finally:
            if connection is not None:
                try:
                    if protocol == "imap": connection.logout()
                    elif protocol == "pop3": connection.close()  # QUIT só é explícito na exclusão.
                    else: connection.close()
                except Exception: pass
