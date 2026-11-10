import io
from pathlib import Path
import zipfile

import pytest

from onmyoji_daemon.mcp.mercado.exception import MercadoError
from onmyoji_daemon.mcp.mercado.reports import ReportReader
from onmyoji_daemon.mcp.mercado.service import MercadoService


DEFAULTS = {"page_size": 2, "max_page_size": 100, "max_report_bytes": 1024 * 1024, "report_cache_ttl_seconds": 900}


class OAuth:
    configured = {"site_id": "MLB"}


class Client:
    provider = "mercadolivre"
    profile = "pessoal"
    defaults = DEFAULTS
    oauth = OAuth()

    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def user_id(self):
        return "authenticated-user"

    def request(self, method, path, **values):
        self.calls.append((method, path, values))
        return self.responses.pop(0) if self.responses else {}


def test_compras_derivam_comprador_da_autorizacao(tmp_path):
    client = Client([{"results": [{"id": 1}], "paging": {"total": 1}}])
    result = MercadoService(tmp_path)._mercadolivre(client, "mercadolivre.purchases_list", {"limit": 2})
    assert client.calls[0][2]["query"]["buyer"] == "authenticated-user"
    assert result["items"] == [{"id": 1}]


def test_atualizacao_de_conta_nao_repete_escrita(tmp_path):
    client = Client([{"id": "authenticated-user"}])
    MercadoService(tmp_path)._mercadolivre(client, "mercadolivre.account_update", {"first_name": "Rodrigo", "confirm": True})
    assert client.calls == [("PUT", "/users/authenticated-user", {"body": {"first_name": "Rodrigo"}, "retry": False})]


def test_escrita_sem_confirmacao_e_rejeitada(tmp_path):
    with pytest.raises(MercadoError) as captured:
        MercadoService(tmp_path).invoke("pessoal", "mercadolivre.favorite_add", {"item_id": "MLB1"})
    assert captured.value.code == "confirmation_required"


def test_billing_aceita_somente_grupos_do_schema():
    from onmyoji_daemon.mcp.mercado.contracts import tool_schema
    assert tool_schema("mercadolivre.billing_periods_list")["properties"]["group"]["enum"] == ["ML", "MP"]


def test_csv_bom_multilinha_e_cursor(tmp_path):
    reader = ReportReader(tmp_path, DEFAULTS)
    content = '\ufeffID,DESCRIPTION,AMOUNT\r\n1,"linha 1\nlinha 2",10.50\r\n2,simples,20.00\r\n3,fim,30.00\r\n'.encode("utf-8")
    path = reader.cache("pessoal", "r1", content)
    first = reader.rows("pessoal", "released_money", "r1", path, 2, None, ",")
    assert first["items"][0]["DESCRIPTION"] == "linha 1\nlinha 2"
    assert first["items"][0]["AMOUNT"] == "10.50"
    second = reader.rows("pessoal", "released_money", "r1", path, 2, first["page"]["nextCursor"], ",")
    assert [row["ID"] for row in second["items"]] == ["3"]


def test_zip_rejeita_path_traversal_e_multiplos_arquivos(tmp_path):
    reader = ReportReader(tmp_path, DEFAULTS)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("../report.csv", "A\n1\n")
    with pytest.raises(MercadoError) as traversal:
        reader.cache("pessoal", "r1", stream.getvalue())
    assert traversal.value.code == "invalid_report_file"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("one.csv", "A\n1\n")
        archive.writestr("two.csv", "A\n2\n")
    with pytest.raises(MercadoError):
        reader.cache("pessoal", "r2", stream.getvalue())


def test_cursor_adulterado_e_rejeitado(tmp_path):
    reader = ReportReader(tmp_path, DEFAULTS)
    path = reader.cache("pessoal", "r1", b"A\n1\n2\n3\n")
    first = reader.rows("pessoal", "account_money", "r1", path, 1, None, ",")
    cursor = first["page"]["nextCursor"]
    with pytest.raises(MercadoError) as captured:
        reader.rows("pessoal", "account_money", "r1", path, 1, cursor[:-1] + ("A" if cursor[-1] != "A" else "B"), ",")
    assert captured.value.code == "invalid_request"


def test_cache_fica_exclusivamente_em_tmp(tmp_path):
    path = ReportReader(tmp_path, DEFAULTS).cache("pessoal", "r1", b"A\n1\n")
    assert path.resolve().is_relative_to(tmp_path / "tmp")
