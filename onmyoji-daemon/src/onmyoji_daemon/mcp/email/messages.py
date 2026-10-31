"""MIME e arquivos de atividade, restritos às raízes autorizadas."""
from __future__ import annotations

from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, make_msgid, getaddresses, formataddr
from html.parser import HTMLParser
from pathlib import Path
import mimetypes
import os

from .contracts import require, text


class PlainHTML(HTMLParser):
    """Extrai texto sem interpretar recursos, links ou scripts."""
    def __init__(self):
        super().__init__(); self.parts = []; self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}: self.hidden += 1
        if tag in {"p", "br", "div", "li"}: self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}: self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)


def parse_message(raw: bytes, maximum: int) -> EmailMessage:
    require(len(raw) <= maximum, "message_too_large", "Mensagem excede o limite configurado.")
    return BytesParser(policy=policy.default).parsebytes(raw)


def attachment_parts(message: EmailMessage) -> list:
    result = []
    def visit(part):
        if part.get_content_disposition() == "attachment" or part.get_filename() or part.get_content_type() == "message/rfc822":
            result.append(part)
        elif part.is_multipart():
            for child in part.iter_parts(): visit(child)
        elif part.get("Content-ID"):
            result.append(part)
    visit(message)
    return result


def part_bytes(part) -> bytes:
    if part.get_content_type() == "message/rfc822":
        return b"\r\n".join(item.as_bytes(policy=policy.SMTP) for item in part.get_payload())
    return part.get_payload(decode=True) or b""


def metadata(message: EmailMessage) -> dict:
    return {key: str(message.get(header, "")) for key, header in
            (("subject", "Subject"), ("from", "From"), ("to", "To"), ("cc", "Cc"),
             ("date", "Date"), ("message_id", "Message-ID"), ("in_reply_to", "In-Reply-To"))}


def content(message: EmailMessage, limit: int) -> dict:
    body = message.get_body(preferencelist=("plain", "html"))
    decoded = ""
    if body is not None:
        raw = body.get_payload(decode=True) or b""
        try: decoded = raw.decode(body.get_content_charset() or "utf-8", errors="replace")
        except LookupError: decoded = raw.decode("utf-8", errors="replace")
        if body.get_content_type() == "text/html":
            parser = PlainHTML(); parser.feed(decoded); decoded = "".join(parser.parts)
    return {**metadata(message), "text": decoded[:limit], "truncated": len(decoded) > limit,
            "untrusted_content": True, "attachments": attachments(message)}


def attachments(message: EmailMessage) -> list[dict]:
    return [{"attachment_id": str(index), "filename": part.get_filename() or "attachment",
             "content_type": part.get_content_type(), "content_id": str(part.get("Content-ID", "")),
             "bytes": len(part_bytes(part))} for index, part in enumerate(attachment_parts(message), 1)]


def authorized_path(value: str, roots: list[str], *, reading: bool) -> Path:
    value = text(value, "Caminho", 4096)
    path = Path(value).expanduser()
    require(path.is_absolute(), "path_not_allowed", "Use um caminho absoluto dentro das raízes autorizadas.")
    require(not path.is_symlink(), "path_not_allowed", "O arquivo não pode ser um link simbólico.")
    resolved = path.resolve()
    allowed = [Path(root).expanduser().resolve() for root in roots]
    require(bool(allowed) and any(resolved.is_relative_to(root) and resolved != root for root in allowed), "path_not_allowed", "Caminho fora das raízes autorizadas.")
    if reading: require(resolved.is_file(), "file_not_found", "Arquivo de entrada não encontrado.")
    else: require(not resolved.exists(), "file_exists", "O destino já existe; escolha outro arquivo.")
    return resolved


def write_file(path: Path, data: bytes) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    # O_EXCL impede sobrescrita inclusive se o destino mudar após a validação.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream: stream.write(data)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"output_path": str(path), "bytes": len(data)}


def addresses(values, label: str) -> list[str]:
    require(isinstance(values, list) and len(values) <= 100, "invalid_address", f"{label} deve ser uma lista de até 100 endereços.")
    for value in values: text(value, label, 400)
    parsed = getaddresses(values)
    require(len(parsed) == len(values) and all(address and "@" in address and not any(c.isspace() for c in address) for _, address in parsed), "invalid_address", f"{label} inválido.")
    return [address for _, address in parsed]


def compose(account: dict, args: dict, limits: dict, attachment_cache: dict[str, bytes] | None = None) -> tuple[EmailMessage, list[str]]:
    message = EmailMessage(policy=policy.SMTP)
    sender = args.get("from_address", account["address"])
    addresses([sender], "Remetente")
    require(sender.casefold() in {v.casefold() for v in [account["address"], *account["allowed_senders"]]}, "sender_not_allowed", "Remetente não configurado nesta conta.")
    message["From"] = formataddr((account["display_name"], sender))
    recipients = []
    for key in ("to", "cc", "bcc"):
        values = args.get(key, [])
        recipients += addresses(values, key)
        if values and key != "bcc": message[key.title()] = ", ".join(values)
    require(bool(recipients), "recipient_required", "Informe pelo menos um destinatário.")
    message["Subject"] = text(args.get("subject", "(sem assunto)"), "Assunto", 998)
    message["Date"] = formatdate(localtime=False); message["Message-ID"] = make_msgid()
    body = args.get("text", "")
    require(isinstance(body, str), "invalid_body", "Corpo textual inválido.")
    message.set_content(body)
    if args.get("html"):
        require(isinstance(args["html"], str), "invalid_body", "Corpo HTML inválido.")
        message.add_alternative(args["html"], subtype="html")
    for key, header in (("in_reply_to", "In-Reply-To"), ("references", "References")):
        if args.get(key): message[header] = text(args[key], header, 4000)
    paths = args.get("attachments", [])
    require(isinstance(paths, list) and len(paths) <= 100, "invalid_argument", "Lista de anexos inválida.")
    total = 0
    for value in paths:
        path = authorized_path(value, account["readable_roots"], reading=True)
        require(path.stat().st_size <= limits["max_attachment_bytes"], "attachment_too_large", "Anexo excede o limite configurado.")
        data = attachment_cache[str(path)] if attachment_cache is not None else path.read_bytes()
        total += len(data)
        require(len(data) <= limits["max_attachment_bytes"] and total <= limits["max_message_bytes"], "message_too_large", "Anexos excedem o limite configurado.")
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        main, sub = mime.split("/", 1)
        message.add_attachment(data, maintype=main, subtype=sub, filename=path.name)
    require(len(message.as_bytes()) <= limits["max_message_bytes"], "message_too_large", "Mensagem MIME excede o limite configurado.")
    return message, list(dict.fromkeys(recipients))
