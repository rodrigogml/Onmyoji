"""Escolha de backend conforme recursos solicitados, sem abrir Excel nos testes."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.excel.config.configuration import DEFAULTS
from onmyoji_daemon.mcp.excel.exception import ExcelExceptions
from onmyoji_daemon.mcp.excel.service import execution, inspection, native, text_inspection


def context_for(path):
    return SimpleNamespace(profile={"allow_write": True}, defaults=DEFAULTS,
                           input_path=Mock(return_value=path), progress=Mock())


@pytest.mark.skipif(os.name != "nt", reason="A seleção de execução nativa requer Windows.")
@pytest.mark.parametrize("operation,arguments", [
    ("navigate", {"kind": "model"}),
    ("navigate", {"kind": "pivots"}),
    ("navigate", {"kind": "connections"}),
    ("read_range", {"sheet": "Dados", "range": "A1", "fields": ["display_text"]}),
])
def test_auto_uses_native_for_requested_object_or_rendered_text(tmp_path, monkeypatch, operation, arguments):
    source = tmp_path / "source.xlsx"; source.write_bytes(b"PK\x03\x04")
    direct = Mock(side_effect=AssertionError("A solicitação precisa do modelo de objetos."))
    desktop = Mock(return_value={"tested": True})
    monkeypatch.setattr(inspection, "execute_direct", direct)
    monkeypatch.setattr(native, "execute_native", desktop)
    result = execution.execute(operation, {"input_path": str(source), **arguments}, context_for(source))
    assert result["backend"] == "native" and desktop.call_count == 1 and direct.call_count == 0


@pytest.mark.skipif(os.name != "nt", reason="A seleção de execução nativa requer Windows.")
def test_auto_falls_back_for_reference_that_direct_cannot_resolve(tmp_path, monkeypatch):
    source = tmp_path / "source.xlsx"; source.write_bytes(b"PK\x03\x04")
    monkeypatch.setattr(inspection, "execute_direct", Mock(side_effect=ExcelExceptions("direct_read_unavailable", "Referência exige Excel.")))
    desktop = Mock(return_value={"cells": []})
    monkeypatch.setattr(native, "execute_native", desktop)
    result = execution.execute("read_range", {"input_path": str(source), "sheet": "Dados", "range": "Tabela[Valor]"}, context_for(source))
    assert result["backend"] == "native" and desktop.call_count == 1
    assert source.read_bytes() == b"PK\x03\x04"


@pytest.mark.parametrize("backend,code", [("auto", "invalid_package"), ("direct", "direct_read_unavailable")])
def test_invalid_package_and_explicit_direct_never_launch_native(tmp_path, monkeypatch, backend, code):
    source = tmp_path / "source.xlsx"; source.write_bytes(b"PK\x03\x04")
    monkeypatch.setattr(inspection, "execute_direct", Mock(side_effect=ExcelExceptions(code, "Contrato indisponível.")))
    desktop = Mock(side_effect=AssertionError("Não deve iniciar Excel."))
    monkeypatch.setattr(native, "execute_native", desktop)
    with pytest.raises(ExcelExceptions) as failure:
        execution.execute("read_range", {"input_path": str(source), "backend": backend, "sheet": "Dados", "range": "A1"}, context_for(source))
    assert failure.value.code == code and desktop.call_count == 0


def test_csv_extension_cannot_override_excel_zip_signature(tmp_path, monkeypatch):
    source = tmp_path / "misnamed.csv"; source.write_bytes(b"PK\x03\x04Excel")
    direct = Mock(return_value={"format": "xlsx"})
    text = Mock(side_effect=AssertionError("Um ZIP Excel não é CSV."))
    monkeypatch.setattr(inspection, "execute_direct", direct)
    monkeypatch.setattr(text_inspection, "execute_text", text)
    result = execution.execute("inspect", {"input_path": str(source)}, context_for(source))
    assert result["format"] == "xlsx" and direct.call_count == 1 and text.call_count == 0


@pytest.mark.skipif(os.name != "nt", reason="A seleção de execução nativa requer Windows.")
def test_compound_excel_routes_read_to_native_but_inspection_remains_direct(tmp_path, monkeypatch):
    source = tmp_path / "binary.xlsx"; source.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    direct, desktop = Mock(return_value={"format": "encrypted_ooxml"}), Mock(return_value={"cells": []})
    monkeypatch.setattr(inspection, "execute_direct", direct)
    monkeypatch.setattr(native, "execute_native", desktop)
    execution.execute("inspect", {"input_path": str(source)}, context_for(source))
    execution.execute("read_range", {"input_path": str(source), "sheet": "Dados", "range": "A1"}, context_for(source))
    assert direct.call_count == 1 and desktop.call_count == 1
