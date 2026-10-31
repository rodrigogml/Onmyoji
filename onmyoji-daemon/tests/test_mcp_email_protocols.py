"""Exercita os clientes reais contra servidores TLS locais e determinísticos."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import ipaddress
import socketserver
import ssl
import threading

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from onmyoji_daemon.mcp.email.configuration import empty_configuration
from onmyoji_daemon.mcp.email.connections import Connections
from onmyoji_daemon.mcp.email.mailbox import Mailbox
from onmyoji_daemon.mcp.email.contracts import EmailError


RAW = b"From: a@example.test\r\nTo: b@example.test\r\nSubject: Test\r\nMessage-ID: <one@example.test>\r\n\r\nBody\r\n"


@pytest.fixture
def protocol_server(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False).sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "server.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); server_context.load_cert_chain(cert_path, key_path)
    original_context = ssl.create_default_context
    monkeypatch.setattr("onmyoji_daemon.mcp.email.connections.ssl.create_default_context", lambda: original_context(cafile=str(cert_path)))
    monkeypatch.setattr("onmyoji_daemon.mcp.email.connections.KeePassCredentialResolver.resolve", lambda *args: "test-password")
    servers = []

    def start(protocol, security="tls", reject_starttls=False):
        events = []
        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True
            def get_request(self):
                sock, peer = super().get_request()
                return (server_context.wrap_socket(sock, server_side=True) if security == "tls" else sock), peer
        class Handler(socketserver.StreamRequestHandler):
            def send(self, value): self.wfile.write(value + b"\r\n"); self.wfile.flush()
            def handle(self):
                self.connection.settimeout(5)
                self.send(b"* OK IMAP4rev1 ready" if protocol == "imap" else b"+OK POP3 ready" if protocol == "pop3" else b"220 localhost SMTP")
                try:
                    while line := self.rfile.readline():
                        line = line.rstrip(b"\r\n"); parts = line.split(); command = parts[0].upper()
                        events.append((parts[1] if protocol == "imap" else command).decode().upper())
                        tls_command = parts[1].upper() if protocol == "imap" else command
                        if tls_command in {b"STARTTLS", b"STLS"}:
                            if reject_starttls:
                                self.send(parts[0] + b" NO disabled" if protocol == "imap" else b"-ERR disabled" if protocol == "pop3" else b"454 disabled")
                                continue
                            self.send(parts[0] + b" OK upgrade" if protocol == "imap" else b"+OK upgrade" if protocol == "pop3" else b"220 upgrade")
                            self.connection = server_context.wrap_socket(self.connection, server_side=True)
                            self.rfile = self.connection.makefile("rb"); self.wfile = self.connection.makefile("wb")
                            continue
                        if protocol == "imap":
                            tag, command = parts[0], parts[1].upper()
                            if command == b"CAPABILITY": self.send(b"* CAPABILITY IMAP4rev1 UIDPLUS MOVE STARTTLS")
                            elif command == b"EXAMINE" or command == b"SELECT":
                                self.send(b"* FLAGS (\\Seen)"); self.send(b"* 1 EXISTS"); self.send(b"* OK [UIDVALIDITY 42] UIDs valid")
                            elif command == b"UID":
                                if parts[2].upper() == b"SEARCH": self.send(b"* SEARCH 1")
                                elif parts[2].upper() == b"FETCH":
                                    self.send(b"* 1 FETCH (UID 1 FLAGS () RFC822.SIZE " + str(len(RAW)).encode() + b" BODY[] {" + str(len(RAW)).encode() + b"}")
                                    self.wfile.write(RAW + b")\r\n"); self.wfile.flush()
                            elif command == b"LOGOUT": self.send(b"* BYE"); self.send(tag + b" OK logout"); break
                            self.send(tag + b" OK completed")
                        elif protocol == "pop3":
                            if command in {b"USER", b"PASS", b"RSET", b"DELE"}: self.send(b"+OK")
                            elif command == b"CAPA": self.send(b"+OK\r\nUIDL\r\nTOP\r\nSTLS\r\n.")
                            elif command == b"STAT": self.send(b"+OK 1 " + str(len(RAW)).encode())
                            elif command == b"UIDL": self.send(b"+OK\r\n1 stable-id\r\n.")
                            elif command in {b"TOP", b"RETR"}: self.send(b"+OK\r\n" + RAW + b".")
                            elif command == b"QUIT": self.send(b"+OK"); break
                        else:
                            if command == b"EHLO": self.send(b"250-localhost\r\n250-STARTTLS\r\n250 AUTH PLAIN LOGIN")
                            elif command == b"AUTH": self.send(b"235 authenticated")
                            elif command in {b"MAIL", b"RCPT", b"RSET"}: self.send(b"250 OK")
                            elif command == b"DATA":
                                self.send(b"354 send data")
                                while self.rfile.readline() != b".\r\n": pass
                                self.send(b"250 queued")
                            elif command == b"QUIT": self.send(b"221 bye"); break
                except (OSError, ssl.SSLError): pass
        server = Server(("127.0.0.1", 0), Handler); thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        servers.append((server, thread)); return server.server_address[1], events
    yield start
    for server, thread in servers: server.shutdown(); server.server_close(); thread.join(timeout=5)


def configured(protocol, port):
    value = {"address": "test@example.test", "folders": {"inbox": "INBOX"}}
    kind = "smtp" if protocol == "smtp" else "incoming"
    value[kind] = {"protocol": protocol, "host": "127.0.0.1", "port": port, "security": "tls", "username": "test@example.test",
                   "auth": {"mode": "password", "credential_profile": "test", "credential_entry_path": "Email/test"}}
    return value


@pytest.mark.parametrize("protocol", ["imap", "pop3", "smtp"])
@pytest.mark.parametrize("security", ["tls", "starttls"])
def test_real_protocol_clients_use_validated_tls_and_preserve_messages(tmp_path, protocol_server, protocol, security):
    port, events = protocol_server(protocol, security); value = configured(protocol, port)
    value["smtp" if protocol == "smtp" else "incoming"]["security"] = security
    connections = Connections(tmp_path, value, empty_configuration()["defaults"])
    if protocol == "smtp":
        with connections.open("smtp") as client:
            assert client.sendmail("a@example.test", ["b@example.test"], RAW) == {}
        assert "DATA" in events
    else:
        box = Mailbox("default", value, empty_configuration()["defaults"], connections)
        listed = box.listing({})
        assert len(listed["messages"]) == 1
        assert b"Body" in box.raw(listed["messages"][0]["reference"])
        assert "DELE" not in events


@pytest.mark.parametrize("protocol", ["imap", "pop3", "smtp"])
def test_starttls_refusal_prevents_authentication(tmp_path, protocol_server, protocol):
    port, events = protocol_server(protocol, "starttls", reject_starttls=True)
    value = configured(protocol, port); kind = "smtp" if protocol == "smtp" else "incoming"
    value[kind]["security"] = "starttls"
    connections = Connections(tmp_path, value, empty_configuration()["defaults"])
    with pytest.raises(Exception):
        with connections.open(kind): pytest.fail("Não deve autenticar sem TLS")
    assert not {"LOGIN", "AUTH", "USER", "PASS"} & set(events)
