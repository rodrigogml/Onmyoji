"""PKI local e privada do Gateway de Mini Apps."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path
import os
import shutil
import ssl
import subprocess

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _write_private(path: Path, value: rsa.RSAPrivateKey) -> None:
    path.write_bytes(value.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    if os.name != "nt": path.chmod(0o600)


def ensure_material(directory: Path) -> tuple[Path, Path, Path]:
    """Cria CA e certificado localhost uma vez; retorna CA, cert e chave."""
    directory.mkdir(parents=True, exist_ok=True)
    ca_cert, ca_key, cert, key = (directory / name for name in ("ca.pem", "ca-key.pem", "gateway.pem", "gateway-key.pem"))
    if all(path.is_file() for path in (ca_cert, ca_key, cert, key)): return ca_cert, cert, key
    now = datetime.now(timezone.utc)
    ca_private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    ca_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Onmyoji Mini Apps Local CA")])
    ca_public = (x509.CertificateBuilder().subject_name(ca_subject).issuer_name(ca_subject).public_key(ca_private.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=3650)).add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True).sign(ca_private, hashes.SHA256()))
    server_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    server_public = (x509.CertificateBuilder().subject_name(server_subject).issuer_name(ca_public.subject).public_key(server_private.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=825)).add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ip_address("127.0.0.1")), x509.IPAddress(ip_address("::1"))]), critical=False).add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True).sign(ca_private, hashes.SHA256()))
    _write_private(ca_key, ca_private); ca_cert.write_bytes(ca_public.public_bytes(serialization.Encoding.PEM)); _write_private(key, server_private); cert.write_bytes(server_public.public_bytes(serialization.Encoding.PEM))
    return ca_cert, cert, key


def server_context(directory: Path) -> ssl.SSLContext:
    _ca, cert, key = ensure_material(directory)
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH); context.minimum_version = ssl.TLSVersion.TLSv1_2; context.load_cert_chain(cert, key)
    return context


def install_trust(ca_cert: Path) -> None:
    """Instala confiança local somente quando chamado explicitamente pelo setup."""
    if os.name == "nt":
        result = subprocess.run(["certutil", "-user", "-addstore", "Root", str(ca_cert)], text=True, capture_output=True, check=False)
        if result.returncode: raise RuntimeError(result.stderr or result.stdout or "certutil falhou")
        return
    update = shutil.which("update-ca-certificates")
    if not update: raise RuntimeError("Esta distribuição Linux não possui update-ca-certificates.")
    destination = Path("/usr/local/share/ca-certificates") / "onmyoji-mini-apps-local-ca.crt"
    try:
        destination.write_bytes(ca_cert.read_bytes())
    except OSError as error: raise RuntimeError("A instalação da CA exige privilégio administrativo.") from error
    result = subprocess.run([update], text=True, capture_output=True, check=False)
    if result.returncode: raise RuntimeError(result.stderr or result.stdout or "update-ca-certificates falhou")
