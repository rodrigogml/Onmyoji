"""Operações de caixa com referências estáveis e leituras sem efeitos colaterais."""
from __future__ import annotations

import base64
from datetime import date
import json
import time

from .contracts import EmailError, digest, require, text
from .messages import parse_message, metadata


def encode(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()).decode()


def decode(value: str) -> dict:
    try:
        require(isinstance(value, str) and 0 < len(value) <= 8192, "invalid_reference", "Referência de mensagem inválida.")
        data = json.loads(base64.urlsafe_b64decode(value))
        require(isinstance(data, dict), "invalid_reference", "Referência de mensagem inválida.")
        return data
    except (ValueError, TypeError, UnicodeError) as error:
        raise EmailError("invalid_reference", "Referência inválida; liste as mensagens novamente.") from error


def decoded(value):
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


class Mailbox:
    def __init__(self, name, account, limits, connections):
        self.name, self.account, self.limits, self.connections = name, account, limits, connections
        self.binding = digest([name, account.get("incoming")])
        self.protocol = account.get("incoming", {}).get("protocol")

    def folder(self, value=None):
        return text(value or self.account["folders"].get("inbox", "INBOX"), "Pasta")

    def reference(self, folder, validity, uid):
        return encode({"binding": self.binding, "folder": folder, "validity": validity, "uid": uid})

    def select(self, client, folder, writable=False):
        status = client.select_folder(folder, readonly=not writable)
        return int(status[b"UIDVALIDITY"])

    def references(self, client, references, writable=False):
        require(isinstance(references, list) and 1 <= len(references) <= 100, "invalid_reference", "Informe de 1 a 100 referências.")
        data = [decode(value) for value in references]
        require(all(value.get("binding") == self.binding and value.get("folder") == data[0].get("folder") for value in data), "stale_reference", "Referência pertence a outra conta, conexão ou pasta.")
        folder = self.folder(data[0]["folder"])
        validity = self.select(client, folder, writable)
        require(all(value.get("validity") == validity and type(value.get("uid")) is int and value["uid"] > 0 for value in data), "stale_reference", "UIDVALIDITY mudou; liste a pasta novamente.")
        ids = list(dict.fromkeys(value["uid"] for value in data))
        require(set(ids) <= set(client.fetch(ids, ["FLAGS"])), "message_not_found", "Uma das mensagens não existe mais.")
        return folder, ids

    def pop_entries(self, client):
        try:
            lines = client.uidl()[1]
        except Exception as error: raise EmailError("uidl_required", "POP3 sem UIDL não oferece referências persistentes.") from error
        result = [(int(line.split()[0]), line.split()[1].decode("ascii")) for line in lines]
        require(len({uid for _, uid in result}) == len(result), "invalid_response", "POP3 retornou UIDLs duplicados.")
        return result

    def raw(self, reference):
        with self.connections.open("incoming") as client:
            if self.protocol == "imap":
                _, ids = self.references(client, [reference])
                size = client.fetch(ids, ["RFC822.SIZE"])[ids[0]][b"RFC822.SIZE"]
                require(size <= self.limits["max_message_bytes"], "message_too_large", "Mensagem excede o limite configurado.")
                # Solicitação parcial limita também o literal que chega do servidor.
                response = client.fetch(ids, [f"BODY.PEEK[]<0.{self.limits['max_message_bytes'] + 1}>"])[ids[0]]
                raw = next(value for key, value in response.items() if key.startswith(b"BODY["))
            else:
                require(self.protocol == "pop3", "protocol_unavailable", "Recebimento não configurado.")
                ref = decode(reference)
                require(ref.get("binding") == self.binding, "stale_reference", "Referência pertence a outra conta ou conexão.")
                index = next((index for index, uid in self.pop_entries(client) if uid == ref.get("uid")), None)
                require(index is not None, "message_not_found", "Mensagem POP3 não encontrada.")
                raw = b"\r\n".join(client.retr(index)[1]) + b"\r\n"
            require(len(raw) <= self.limits["max_message_bytes"], "message_too_large", "Mensagem excede o limite configurado.")
            return raw

    def query(self, args):
        filters = args.get("filters", {}); criteria = []
        for key, token in (("from", "FROM"), ("to", "TO"), ("subject", "SUBJECT"), ("text", "TEXT")):
            if key in filters: criteria.extend([token, text(filters[key], key)])
        for key, token in (("since", "SINCE"), ("before", "BEFORE")):
            if key in filters: criteria.extend([token, date.fromisoformat(filters[key])])
        if "unread" in filters: criteria.append("UNSEEN" if filters["unread"] else "SEEN")
        if "flagged" in filters: criteria.append("FLAGGED" if filters["flagged"] else "UNFLAGGED")
        return criteria or ["ALL"]

    def listing(self, args):
        folder = self.folder(args.get("folder")); limit = args.get("limit", self.limits["page_size"])
        require(type(limit) is int and 1 <= limit <= 100, "invalid_argument", "Limite deve estar entre 1 e 100.")
        signature = digest([self.binding, folder, args.get("filters", {})])
        cursor = decode(args["cursor"]) if args.get("cursor") else {}
        require(not cursor or cursor.get("signature") == signature, "invalid_cursor", "Cursor pertence a outra consulta.")
        result = []; next_cursor = None
        with self.connections.open("incoming") as client:
            if self.protocol == "imap":
                validity = self.select(client, folder)
                require(not cursor or cursor.get("validity") == validity, "stale_reference", "A pasta mudou; reinicie a listagem.")
                criteria = self.query(args)
                if cursor: criteria += ["UID", f"1:{cursor['before_uid'] - 1}"]
                ids = sorted(client.search(criteria, charset="UTF-8"), reverse=True)
                selected = ids[:limit]
                rows = client.fetch(selected, ["FLAGS", "RFC822.SIZE", "BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO CC DATE MESSAGE-ID)]"]) if selected else {}
                for uid in selected:
                    row = rows.get(uid)
                    if not row: continue
                    header = next((value for key, value in row.items() if key.startswith(b"BODY[")), b"")
                    msg = parse_message(header, self.limits["max_message_bytes"])
                    result.append({**metadata(msg), "reference": self.reference(folder, validity, uid), "bytes": row.get(b"RFC822.SIZE"), "flags": [decoded(flag) for flag in row.get(b"FLAGS", ())]})
                if len(ids) > limit:
                    next_cursor = encode({"signature": signature, "validity": validity, "before_uid": selected[-1]})
                return {"messages": result, "next_cursor": next_cursor, "partial": False}
            require(self.protocol == "pop3", "protocol_unavailable", "Recebimento não configurado.")
            filters = args.get("filters", {})
            require(not any(key in filters for key in ("unread", "flagged")), "unsupported_filter", "POP3 não fornece flags ou estado de leitura.")
            entries = list(reversed(self.pop_entries(client))); snapshot = digest(entries)
            require(not cursor or cursor.get("snapshot") == snapshot, "stale_cursor", "A caixa POP3 mudou; reinicie a listagem.")
            offset = cursor.get("offset", 0); scanned = 0; deadline = time.monotonic() + self.limits["timeout_seconds"]
            while offset < len(entries) and scanned < self.limits["pop_scan_limit"] and len(result) < limit and time.monotonic() < deadline:
                index, uid = entries[offset]; offset += 1; scanned += 1
                try: raw = b"\r\n".join(client.top(index, 0)[1])
                except Exception: raw = b"\r\n".join(client.retr(index)[1])
                msg = parse_message(raw, self.limits["max_message_bytes"])
                if filters.get("text"):
                    raw = b"\r\n".join(client.retr(index)[1]); msg = parse_message(raw, self.limits["max_message_bytes"])
                from .messages import content
                values = metadata(msg)
                matched = all(str(value).casefold() in (content(msg, self.limits["max_message_bytes"])["text"] if key == "text" else values[key]).casefold() for key, value in filters.items() if key in {"from", "to", "subject", "text"})
                if "since" in filters or "before" in filters:
                    from email.utils import parsedate_to_datetime
                    try: stamp = parsedate_to_datetime(str(msg.get("Date", ""))).date()
                    except (ValueError, TypeError): matched = False
                    else:
                        if "since" in filters: matched = matched and stamp >= date.fromisoformat(filters["since"])
                        if "before" in filters: matched = matched and stamp < date.fromisoformat(filters["before"])
                if matched: result.append({**values, "reference": self.reference("INBOX", 0, uid)})
            if offset < len(entries): next_cursor = encode({"signature": signature, "snapshot": snapshot, "offset": offset})
            return {"messages": result, "next_cursor": next_cursor, "partial": next_cursor is not None, "scanned": scanned, "search_scope": "POP3 client-side bounded scan"}

    def invoke(self, operation, args):
        if operation in {"list_messages", "search_messages", "list_drafts"}:
            if operation == "list_drafts": args = {**args, "folder": self.special("drafts")}
            return self.listing(args)
        with self.connections.open("incoming") as client:
            if operation == "check_mailbox" and self.protocol == "pop3":
                count, size = client.stat(); return {"messages": count, "bytes": size, "unread": None}
            if self.protocol == "pop3" and operation == "delete_messages":
                refs = [decode(value) for value in args["references"]]
                require(all(ref.get("binding") == self.binding for ref in refs), "stale_reference", "Referência pertence a outra conta.")
                mapping = {uid: index for index, uid in self.pop_entries(client)}
                require(all(ref.get("uid") in mapping for ref in refs), "message_not_found", "Mensagem POP3 não encontrada.")
                try:
                    for ref in refs: client.dele(mapping[ref["uid"]])
                    client.quit()
                except Exception as error:
                    # RSET quando ainda conectado evita commit parcial de DELE.
                    try: client.rset()
                    except Exception: pass
                    raise EmailError("delete_unknown", "POP3 não confirmou a exclusão; consulte a caixa antes de repetir.") from error
                return {"deleted": len(refs)}
            require(self.protocol == "imap", "protocol_unavailable", "Operação exige recebimento IMAP.")
            folder = self.folder(args.get("folder"))
            if operation == "list_folders":
                return {"folders": [{"name": name, "delimiter": decoded(delimiter), "flags": [decoded(flag) for flag in flags]} for flags, delimiter, name in client.list_folders()]}
            if operation in {"check_mailbox", "get_folder_status"}:
                return {decoded(key).lower(): value for key, value in client.folder_status(folder).items()}
            if operation in {"create_folder", "rename_folder", "delete_folder"}:
                if operation == "create_folder": client.create_folder(folder)
                elif operation == "rename_folder": client.rename_folder(folder, text(args["destination"], "Destino"))
                else:
                    require(client.folder_status(folder)[b"MESSAGES"] == 0, "folder_not_empty", "Exclua ou mova as mensagens antes de remover a pasta.")
                    client.delete_folder(folder)
                return {"completed": True}
            _, ids = self.references(client, args["references"], writable=True)
            if operation == "update_message_flags":
                add, remove = args.get("add", []), args.get("remove", [])
                allowed = {"\\Seen", "\\Answered", "\\Flagged", "\\Draft"}
                require(set(add + remove) <= allowed and not (set(add) & set(remove)), "invalid_flags", "Flags inválidas; exclusão deve usar ferramenta específica.")
                if add: client.add_flags(ids, add, silent=True)
                if remove: client.remove_flags(ids, remove, silent=True)
            elif operation == "copy_messages": client.copy(ids, text(args["destination"], "Destino"))
            elif operation in {"move_messages", "trash_messages", "archive_messages"}:
                destination = args.get("destination") if operation == "move_messages" else self.special("trash" if operation == "trash_messages" else "archive")
                require(client.has_capability("MOVE"), "capability_unavailable", "Servidor não oferece MOVE seguro.")
                client.move(ids, text(destination, "Destino"))
            elif operation in {"delete_messages", "delete_draft"}:
                require(client.has_capability("UIDPLUS"), "capability_unavailable", "Exclusão seletiva exige UIDPLUS; não será executado EXPUNGE global.")
                client.delete_messages(ids, silent=True); client.uid_expunge(ids)
            else: require(False, "unsupported_operation", "Operação de caixa desconhecida.")
            return {"completed": True, "affected": len(ids)}

    def special(self, name):
        folder = self.account["folders"].get(name)
        require(bool(folder), "folder_not_configured", "Configure a pasta especial no setup.")
        return folder

    def append(self, raw, folder, draft=False):
        with self.connections.open("incoming") as client:
            require(self.protocol == "imap", "protocol_unavailable", "APPEND exige IMAP.")
            reply = client.append(folder, raw, flags=(b"\\Draft",) if draft else (b"\\Seen",))
            result = {"saved": True, "folder": folder}
            import re
            match = re.search(r"APPENDUID\s+(\d+)\s+(\d+)", str(decoded(reply)))
            if match: result["reference"] = self.reference(folder, int(match[1]), int(match[2]))
            return result
