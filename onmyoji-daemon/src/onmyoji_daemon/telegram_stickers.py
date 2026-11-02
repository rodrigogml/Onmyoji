"""Catálogo local e versionável de figurinhas permitidas ao Gateway Telegram."""

from __future__ import annotations

import json
import io
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path


SUPPORTED_EXTENSIONS = frozenset({".webp", ".tgs", ".webm"})
STATIC_SOURCE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
STICKER_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
STATIC_STICKER_SIDE = 512
STATIC_STICKER_TARGET_BYTES = 256 * 1024
MAX_STATIC_SOURCE_PIXELS = 50_000_000


@dataclass(frozen=True)
class Sticker:
    """Figurinha local validada, pronta para ser enviada pelo gateway."""

    identifier: str
    path: Path
    context: str


@dataclass(frozen=True)
class StickerCatalog:
    """Resultado da leitura do catálogo, incluindo entradas indisponíveis."""

    folder: Path
    stickers: tuple[Sticker, ...]
    issues: tuple[str, ...]
    raw_entries: dict[str, dict[str, str]]


@dataclass(frozen=True)
class StaticStickerConversion:
    """Resultado de uma conversão não destrutiva para WebP de figurinha."""

    source: Path
    output: Path
    source_size: tuple[int, int]
    output_bytes: int
    quality: int
    reached_target: bool


def sticker_folder(root: Path) -> Path:
    """Retorna a pasta versionável de mídia e catálogo desta instância."""
    return root.resolve() / "configs" / "daemon" / "services" / "telegram" / "stickers"


def catalog_path(root: Path) -> Path:
    """Retorna o arquivo TOML do catálogo de figurinhas."""
    return sticker_folder(root) / "catalog.toml"


def suggested_identifier(path: Path) -> str:
    """Produz uma sugestão estável de ID a partir do nome do arquivo."""
    value = re.sub(r"[^a-z0-9]+", "-", path.stem.casefold()).strip("-")
    return (value or "figurinha")[:64].rstrip("-")


def discover(root: Path) -> list[Path]:
    """Lista somente arquivos de figurinha válidos no diretório imediato."""
    folder = sticker_folder(root)
    if not folder.is_dir() or folder.is_symlink():
        return []
    return sorted((path for path in folder.iterdir() if path.is_file() and not path.is_symlink() and path.suffix.casefold() in SUPPORTED_EXTENSIONS), key=lambda path: path.name.casefold())


def discover_static_sources(root: Path) -> list[Path]:
    """Lista imagens estáticas locais que ainda podem ser convertidas para WebP."""
    folder = sticker_folder(root)
    if not folder.is_dir() or folder.is_symlink():
        return []
    return sorted(
        (path for path in folder.iterdir() if path.is_file() and not path.is_symlink() and path.suffix.casefold() in STATIC_SOURCE_EXTENSIONS),
        key=lambda path: path.name.casefold(),
    )


def optimized_static_path(source: Path) -> Path:
    """Define a saída WebP sem reutilizar nem substituir o arquivo de origem."""
    return source.with_name(f"{source.stem}-telegram.webp")


def convert_static(source: Path, *, target_bytes: int = STATIC_STICKER_TARGET_BYTES) -> StaticStickerConversion:
    """Converte uma imagem estática local em WebP 512x512 sem alterar a origem.

    A imagem é centralizada em tela transparente para atender à dimensão visual
    das figurinhas. A saída é criada exclusivamente se ainda não existir.
    """
    if not source.is_file() or source.is_symlink() or source.suffix.casefold() not in STATIC_SOURCE_EXTENSIONS:
        raise ValueError("A imagem de origem deve ser um arquivo PNG, JPG, JPEG ou WebP regular.")
    target = optimized_static_path(source)
    if target.exists():
        raise FileExistsError(f"A saída já existe: {target.name}.")
    try:
        from PIL import Image
        with Image.open(source) as original:
            original.load()
            width, height = original.size
            if width < 1 or height < 1 or width * height > MAX_STATIC_SOURCE_PIXELS:
                raise ValueError("A imagem excede o limite seguro de pixels para conversão.")
            image = original.convert("RGBA")
    except ImportError as error:
        raise RuntimeError("Pillow não está instalado no runtime. Instale ou repare PDF e OCR básico antes de converter figurinhas.") from error
    except OSError as error:
        raise ValueError(f"Não foi possível ler a imagem: {error}") from error
    image.thumbnail((STATIC_STICKER_SIDE, STATIC_STICKER_SIDE), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (STATIC_STICKER_SIDE, STATIC_STICKER_SIDE), (0, 0, 0, 0))
    canvas.alpha_composite(image, ((STATIC_STICKER_SIDE - image.width) // 2, (STATIC_STICKER_SIDE - image.height) // 2))
    encoded = b""
    quality = 30
    for candidate in range(88, 29, -8):
        stream = io.BytesIO()
        canvas.save(stream, format="WEBP", quality=candidate, method=6)
        encoded, quality = stream.getvalue(), candidate
        if len(encoded) <= target_bytes:
            break
    try:
        with target.open("xb") as output:
            output.write(encoded)
    except OSError as error:
        raise ValueError(f"Não foi possível publicar a figurinha convertida: {error}") from error
    return StaticStickerConversion(source, target, (width, height), len(encoded), quality, len(encoded) <= target_bytes)


def load(root: Path, maximum_bytes: int) -> StickerCatalog:
    """Carrega as entradas válidas e relata falhas sem publicar ativos inválidos."""
    folder, source = sticker_folder(root), catalog_path(root)
    if folder.is_symlink():
        return StickerCatalog(folder, (), ("Pasta de figurinhas não pode ser um link.",), {})
    if not source.exists():
        return StickerCatalog(folder, (), (), {})
    try:
        document = tomllib.loads(source.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        return StickerCatalog(folder, (), (f"Catálogo inválido: {error}",), {})
    if document.get("schema_version") != 1 or not isinstance(document.get("stickers", {}), dict):
        return StickerCatalog(folder, (), ("Catálogo inválido: use schema_version = 1 e a tabela [stickers].",), {})
    raw: dict[str, dict[str, str]] = {}
    valid: list[Sticker] = []
    issues: list[str] = []
    for identifier, value in sorted(document["stickers"].items()):
        if not isinstance(identifier, str) or not STICKER_ID.fullmatch(identifier):
            issues.append(f"ID inválido: {identifier!r}.")
            continue
        if not isinstance(value, dict):
            issues.append(f"{identifier}: entrada inválida.")
            continue
        filename, context = value.get("file"), value.get("context")
        if not isinstance(filename, str) or Path(filename).name != filename or not filename:
            issues.append(f"{identifier}: arquivo deve ser somente um nome dentro da pasta de figurinhas.")
            continue
        if not isinstance(context, str) or not 1 <= len(context.strip()) <= 240:
            issues.append(f"{identifier}: informe um contexto de uso entre 1 e 240 caracteres.")
            raw[identifier] = {"file": filename, "context": context if isinstance(context, str) else ""}
            continue
        raw[identifier] = {"file": filename, "context": context.strip()}
        path = folder / filename
        if path.suffix.casefold() not in SUPPORTED_EXTENSIONS:
            issues.append(f"{identifier}: extensão não suportada ({path.suffix or 'sem extensão'}).")
        elif not path.is_file() or path.is_symlink():
            issues.append(f"{identifier}: arquivo ausente, não regular ou link.")
        elif path.stat().st_size > maximum_bytes:
            issues.append(f"{identifier}: arquivo excede o limite de envio configurado.")
        else:
            valid.append(Sticker(identifier, path.resolve(), context.strip()))
    return StickerCatalog(folder, tuple(valid), tuple(issues), raw)


def save(root: Path, entries: dict[str, dict[str, str]]) -> Path:
    """Grava o catálogo com publicação atômica e ordenada."""
    folder, target = sticker_folder(root), catalog_path(root)
    if folder.is_symlink():
        raise ValueError("Pasta de figurinhas não pode ser um link.")
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["schema_version = 1"]
    for identifier, value in sorted(entries.items()):
        if not STICKER_ID.fullmatch(identifier):
            raise ValueError("ID de figurinha inválido.")
        filename, context = str(value.get("file") or ""), str(value.get("context") or "").strip()
        if Path(filename).name != filename or not filename or not 1 <= len(context) <= 240:
            raise ValueError("Arquivo ou contexto de figurinha inválido.")
        lines.extend(("", f"[stickers.{identifier}]", f"file = {json.dumps(filename, ensure_ascii=False)}", f"context = {json.dumps(context, ensure_ascii=False)}"))
    temporary = target.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(target)
    return target
