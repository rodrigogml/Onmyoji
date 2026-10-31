"""Suíte de e-mail com protocolo, arquivos e submissões sob controle interno."""
from __future__ import annotations

import hashlib
import smtplib
from email import policy
from email.utils import getaddresses, make_msgid, formatdate
from pathlib import Path

from ..configuration import McpConfiguration
from .configuration import validate_configuration, validate_roots
from .connections import Connections
from .contracts import EmailError, digest, identifier, require, text
from .mailbox import Mailbox, decode, decoded
from . import messages
from .store import SubmissionStore


COMMON = {"test_connection", "get_capabilities"}
READ = {"check_mailbox", "list_messages", "search_messages", "get_message", "get_message_headers", "list_attachments", "download_attachment", "export_message", "delete_messages"}
IMAP = {"list_folders", "get_folder_status", "create_folder", "rename_folder", "delete_folder", "update_message_flags", "copy_messages", "move_messages", "trash_messages", "archive_messages", "import_message", "list_drafts", "create_draft", "update_draft", "delete_draft"}
SMTP = {"send_message", "get_submission_status"}
WRITES = {"create_folder", "rename_folder", "delete_folder", "update_message_flags", "copy_messages", "move_messages", "trash_messages", "archive_messages", "delete_messages", "download_attachment", "export_message", "import_message", "create_draft", "update_draft", "delete_draft", "send_draft", "send_message", "reply_message", "forward_message", "start_authorization", "cancel_authorization"}


def operations(account: dict) -> set[str]:
    result = set(COMMON); incoming = account.get("incoming", {}).get("protocol")
    if incoming: result |= READ
    if incoming == "imap": result |= IMAP
    if account.get("smtp"):
        result |= SMTP
        if incoming: result |= {"reply_message", "forward_message"}
        if incoming == "imap": result.add("send_draft")
    if any(value.get("auth", {}).get("mode") == "oauth2" and value["auth"].get("callback_mode") == "remote" for value in (account.get("incoming", {}), account.get("smtp", {}))):
        result |= {"start_authorization", "get_authorization_status", "cancel_authorization"}
    return result


class EmailService:
    def __init__(self, root: Path): self.root = root.resolve()

    def account(self, name, enabled=True):
        config = McpConfiguration(self.root); document = config.email(); validate_configuration(document)
        account = document["accounts"].get(identifier(name))
        require(isinstance(account, dict) and (not enabled or account["enabled"]), "account_unavailable", "Conta indisponível.")
        validate_roots(self.root, {"accounts": {name: account}})
        return account, document["defaults"]

    def test_account(self, name):
        """Diagnóstico administrativo também para contas desabilitadas."""
        try:
            account, limits = self.account(name, enabled=False)
            return self.diagnose(account, Connections(self.root, account, limits), "test_connection")
        except EmailError: raise
        except Exception as error: raise EmailError("connection_failed", "Não foi possível autenticar as conexões da conta.") from error

    def invoke(self, name, operation, args):
        try:
            account, limits = self.account(name)
            require(operation in operations(account), "operation_unavailable", "Operação indisponível para os protocolos desta conta.")
            if operation in WRITES: require(args.get("confirm") is True, "confirmation_required", "Esta operação exige confirm=true.")
            connections = Connections(self.root, account, limits); mailbox = Mailbox(name, account, limits, connections)
            if operation in {"start_authorization", "get_authorization_status", "cancel_authorization"}:
                connection = args["connection"]
                require(connection in {"incoming", "smtp"} and account.get(connection, {}).get("auth", {}).get("callback_mode") == "remote", "remote_mode_required", "Escolha uma conexão com OAuth remoto.")
                from ..mini_apps.service import MiniAppsMcpService
                params = {"integration": "email", "profile": name + "--" + connection, "confirm": args.get("confirm", False)}
                if args.get("session_id"): params["session_id"] = args["session_id"]
                action = {"start_authorization": "start", "get_authorization_status": "status", "cancel_authorization": "cancel"}[operation]
                return MiniAppsMcpService(self.root).invoke("oauth." + action, params)
            if operation in COMMON: return self.diagnose(account, connections, operation)
            if operation == "get_submission_status":
                return SubmissionStore(self.root).status(name, args["request_id"], digest(account["smtp"]))
            if operation in {"get_message", "get_message_headers", "list_attachments", "download_attachment", "export_message"}:
                raw = mailbox.raw(args["reference"]); msg = messages.parse_message(raw, limits["max_message_bytes"])
                if operation == "get_message": return {"reference": args["reference"], **messages.content(msg, limits["max_body_characters"])}
                if operation == "get_message_headers": return {"headers": [{"name": key, "value": str(value)} for key, value in msg.items()], "untrusted_content": True}
                if operation == "list_attachments": return {"attachments": messages.attachments(msg), "untrusted_content": True}
                if operation == "download_attachment":
                    index = int(args["attachment_id"]); parts = messages.attachment_parts(msg)
                    require(1 <= index <= len(parts), "attachment_not_found", "Anexo não encontrado.")
                    raw = messages.part_bytes(parts[index - 1])
                    require(len(raw) <= limits["max_attachment_bytes"], "attachment_too_large", "Anexo excede o limite configurado.")
                path = messages.authorized_path(args["output_path"], account["writable_roots"], reading=False)
                return messages.write_file(path, raw)
            if operation == "import_message":
                path = messages.authorized_path(args["file_path"], account["readable_roots"], reading=True)
                require(path.stat().st_size <= limits["max_message_bytes"], "message_too_large", "Arquivo excede o limite configurado.")
                raw = path.read_bytes(); messages.parse_message(raw, limits["max_message_bytes"])
                return mailbox.append(raw, mailbox.folder(args.get("folder")))
            if operation in {"create_draft", "update_draft"}:
                if operation == "update_draft":
                    with connections.open("incoming") as client:
                        folder, ids = mailbox.references(client, [args["reference"]], writable=True)
                        require(folder == mailbox.special("drafts"), "invalid_draft", "Referência não pertence à pasta de rascunhos.")
                        require(client.has_capability("UIDPLUS"), "capability_unavailable", "Substituir rascunho exige UIDPLUS.")
                msg, recipients = messages.compose(account, args, limits)
                if args.get("bcc"): msg["Bcc"] = ", ".join(args["bcc"])
                result = mailbox.append(msg.as_bytes(), mailbox.special("drafts"), draft=True)
                if operation == "update_draft":
                    try: mailbox.invoke("delete_draft", {"references": [args["reference"]]})
                    except Exception: result["previous_draft_removed"] = False
                    else: result["previous_draft_removed"] = True
                result["message_id"] = str(msg["Message-ID"])
                return result
            if operation in {"send_message", "reply_message", "forward_message", "send_draft"}:
                return self.send(name, account, limits, connections, mailbox, operation, args)
            if operation == "delete_draft":
                ref = decode(args["reference"])
                require(ref.get("folder") == mailbox.special("drafts"), "invalid_draft", "Referência não pertence à pasta de rascunhos.")
                args = {**args, "references": [args["reference"]]}
            return mailbox.invoke(operation, args)
        except EmailError: raise
        except Exception as error:
            raise EmailError("email_operation_failed", "Operação de e-mail não concluída. Verifique conexão, autenticação e parâmetros.") from error

    def diagnose(self, account, connections, operation):
        result = {}
        for kind in ("incoming", "smtp"):
            if not account.get(kind): continue
            protocol = account[kind]["protocol"]
            with connections.open(kind) as client:
                if protocol == "pop3":
                    try: capabilities = client.capa()
                    except Exception: capabilities = []
                else: capabilities = client.capabilities() if protocol == "imap" else client.esmtp_features
                result[kind] = {"authenticated": True, "protocol": protocol, "capabilities": [decoded(value) for value in capabilities]}
        return {"connections": result, "operations": sorted(operations(account)), "authenticated": True}

    def send(self, name, account, limits, connections, mailbox, operation, args):
        request_id = text(args.get("request_id"), "request_id", 200)
        fingerprint_args = {key: value for key, value in args.items() if key not in {"confirm", "account"}}
        hashes = []; attachment_cache = {}; total_attachment_bytes = 0
        for value in args.get("attachments", []):
            path = messages.authorized_path(value, account["readable_roots"], reading=True)
            require(path.stat().st_size <= limits["max_attachment_bytes"], "attachment_too_large", "Anexo excede o limite.")
            data = path.read_bytes(); total_attachment_bytes += len(data)
            require(len(data) <= limits["max_attachment_bytes"] and total_attachment_bytes <= limits["max_message_bytes"], "attachment_too_large", "Anexos excedem o limite configurado.")
            attachment_cache[str(path)] = data
            hashes.append(hashlib.sha256(data).hexdigest())
        store = SubmissionStore(self.root); binding = digest(account["smtp"])
        fingerprint = digest([operation, fingerprint_args, hashes])
        previous = store.previous(name, request_id, binding, fingerprint)
        if previous: return previous
        # Construção MIME precede begin; erros locais não reservam a chave de envio.
        if operation == "send_draft":
            reference = decode(args["reference"])
            require(reference.get("folder") == mailbox.special("drafts"), "invalid_draft", "Mensagem não é um rascunho da conta.")
            msg = messages.parse_message(mailbox.raw(args["reference"]), limits["max_message_bytes"])
            recipients = [address for _, address in getaddresses(msg.get_all("To", []) + msg.get_all("Cc", []) + msg.get_all("Bcc", []))]
            recipients = messages.addresses(recipients, "Destinatários do rascunho")
            require(bool(recipients), "recipient_required", "Rascunho sem destinatários.")
            from_pairs = getaddresses(msg.get_all("From", []))
            require(len(from_pairs) == 1, "sender_not_allowed", "Rascunho deve conter um único remetente.")
            sender = from_pairs[0][1]
            require(sender.casefold() in {value.casefold() for value in [account["address"], *account["allowed_senders"]]}, "sender_not_allowed", "Remetente do rascunho não autorizado.")
            if "Bcc" in msg: del msg["Bcc"]
            msg.replace_header("Message-ID", make_msgid()) if "Message-ID" in msg else msg.__setitem__("Message-ID", make_msgid())
            msg.replace_header("Date", formatdate()) if "Date" in msg else msg.__setitem__("Date", formatdate())
        else:
            compose_args = dict(args)
            original = None
            if operation in {"reply_message", "forward_message"}:
                original = messages.parse_message(mailbox.raw(args["reference"]), limits["max_message_bytes"])
                if operation == "reply_message":
                    from email.utils import formataddr
                    compose_args.setdefault("to", [formataddr(pair) for pair in getaddresses(original.get_all("Reply-To", original.get_all("From", [])))])
                    compose_args.setdefault("subject", "Re: " + str(original.get("Subject", "")))
                    compose_args["in_reply_to"] = str(original.get("Message-ID", ""))
                    compose_args["references"] = (str(original.get("References", "")) + " " + str(original.get("Message-ID", ""))).strip()
                else:
                    compose_args.setdefault("subject", "Fwd: " + str(original.get("Subject", "")))
                    if args.get("forward_format", "attachment") == "inline":
                        compose_args["text"] = args.get("text", "") + "\n\n" + messages.content(original, limits["max_body_characters"])["text"]
            msg, recipients = messages.compose(account, compose_args, limits, attachment_cache)
            sender = getaddresses(msg.get_all("From", []))[0][1]
            if operation == "forward_message":
                if args.get("forward_format", "attachment") == "attachment": msg.add_attachment(original)
        international = any(not value.isascii() for value in [sender, *recipients])
        raw = msg.as_bytes(policy=policy.SMTPUTF8 if international else policy.SMTP)
        require(len(raw) <= limits["max_message_bytes"], "message_too_large", "Mensagem excede o limite configurado.")
        previous = store.begin(name, request_id, binding, fingerprint)
        if previous: return previous
        attempted = False
        result = {"request_id": request_id, "message_id": str(msg["Message-ID"]), "delivery_confirmed": False}
        try:
            with connections.open("smtp") as client:
                options = ["SMTPUTF8"] if any(not value.isascii() for value in [sender, *recipients]) else []
                require(not options or client.has_extn("smtputf8"), "smtputf8_unavailable", "Servidor não oferece SMTPUTF8.")
                attempted = True
                refused = client.sendmail(sender, recipients, raw, mail_options=options)
                result.update(status="partially_accepted" if refused else "accepted", accepted_recipients=[value for value in recipients if value not in refused], rejected_recipients=list(refused))
        except smtplib.SMTPRecipientsRefused:
            result["status"] = "rejected"
        except smtplib.SMTPResponseException:
            result["status"] = "rejected"
        except Exception:
            result["status"] = "unknown" if attempted else "rejected"
        result["retry_allowed"] = False
        # Persistir aceite antes de APPEND; falhas de pós-processamento jamais reenviam.
        store.finish(name, request_id, result)
        if result["status"] in {"accepted", "partially_accepted"}:
            if account["sent_copy"] == "append":
                try: mailbox.append(raw, mailbox.special("sent")); result["sent_copy_saved"] = True
                except Exception: result["sent_copy_saved"] = False
            if operation == "send_draft" and result["status"] == "accepted":
                try: mailbox.invoke("delete_draft", {"references": [args["reference"]]}); result["draft_removed"] = True
                except Exception: result["draft_removed"] = False
        return store.finish(name, request_id, result)
