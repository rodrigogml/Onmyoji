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


def test_chargeback_uses_authenticated_caller_header(tmp_path):
    client = Client([{"id": "1"}]); client.provider = "mercadopago"
    MercadoService(tmp_path)._mercadopago(client, "mercadopago.chargeback_get", {"chargeback_id": "1"})
    assert client.calls[0][2]["caller_id"] is True


def test_escrita_sem_confirmacao_e_rejeitada(tmp_path):
    with pytest.raises(MercadoError) as captured:
        MercadoService(tmp_path).invoke("pessoal", "mercadolivre.favorite_add", {"item_id": "MLB1"})
    assert captured.value.code == "confirmation_required"


def test_billing_aceita_somente_grupos_do_schema():
    from onmyoji_daemon.mcp.mercado.contracts import tool_schema
    assert tool_schema("mercadolivre.billing_periods_list")["properties"]["group"]["enum"] == ["ML", "MP"]


def test_billing_uses_official_period_paths(tmp_path):
    client = Client([{"results": [], "paging": {"total": 0}}])
    MercadoService(tmp_path)._mercadolivre(client, "mercadolivre.billing_documents_list", {"group": "MP", "period": "2026-11-01", "limit": 2})
    assert client.calls[0][1] == "/billing/integration/periods/key/2026-11-01/documents"


def test_report_paths_distinguish_legacy_and_activities(tmp_path):
    service = MercadoService(tmp_path)
    client = Client([[]]); client.provider = "mercadopago"
    service._reports(client, "mercadopago.reports_list", {"family": "released_money", "limit": 2})
    assert client.calls[0][1] == "/v1/account/release_report/list"
    client = Client([{"results": []}]); client.provider = "mercadopago"
    service._reports(client, "mercadopago.reports_list", {"family": "activities_collection", "limit": 2})
    assert client.calls[0][1] == "/v1/reporting/operations/activities_collection/statements"


def test_activity_report_creation_uses_filters_and_csv(tmp_path):
    client = Client([{"record_id": "id"}]); client.provider = "mercadopago"
    MercadoService(tmp_path)._reports(client, "mercadopago.report_create", {"family": "activities_withdraw", "date_from": "2026-11-01T00:00:00-03:00", "date_to": "2026-11-02T00:00:00-03:00", "confirm": True})
    method, path, values = client.calls[0]
    assert (method, path) == ("POST", "/v1/reporting/operations/activities_withdraw/statements")
    assert values["body"]["file_format"] == "application/csv"


def test_report_configuration_rejects_column_from_another_family():
    service = MercadoService(Path("."))
    with pytest.raises(MercadoError) as captured:
        service._report_configuration_body("activities_withdraw", {"columns": ["buyer_document"]})
    assert captured.value.code == "invalid_request"
    body = service._report_configuration_body("activities_withdraw", {"columns": ["date_created", "bank_account"]})
    assert body["notifiers"] == [{"type": "internal_sftp", "data": {}}]


def test_activity_configuration_update_targets_structure_id(tmp_path):
    client = Client([{"structure": {"id": "structure-1"}, "notifiers": [{"id": "internal-1", "type": "internal_sftp"}, {"id": "external-1", "type": "webhook"}]}, {"ok": True}]); client.provider = "mercadopago"
    MercadoService(tmp_path)._reports(client, "mercadopago.report_configuration_set", {"family": "activities_withdraw", "columns": ["date_created", "bank_account"], "confirm": True})
    assert client.calls[1][0:2] == ("PUT", "/v1/reporting/operations/activities_withdraw/config/structure-1")
    assert client.calls[1][2]["body"]["notifiers"] == [{"type": "internal_sftp", "data": {}, "id": "internal-1"}]


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
    assert ReportReader(tmp_path, DEFAULTS).cached("pessoal", "r1") == path
