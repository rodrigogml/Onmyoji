import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omie import ENDPOINTS, OmieError, build_params, call_api, load_settings, main


class OmieTests(unittest.TestCase):
    def profile(self):
        handle, filename = tempfile.mkstemp(suffix=".ini")
        os.close(handle)
        Path(filename).write_text('''schema_version = 1
[defaults]
timeout_seconds = 30
max_retries = 2
[profiles.laveli]
vault_profile = "vault"
vault_entry_path = "APIs/Omie/laveli"
app_key_field = "username"
app_secret_field = "password"
''', encoding="utf-8")
        self.addCleanup(lambda: Path(filename).unlink(missing_ok=True))
        return filename

    def test_profile_and_pagination(self):
        settings = load_settings(self.profile(), "laveli")
        self.assertEqual(settings.profile, "vault")
        self.assertEqual(build_params("departments.list", {"params": {"page": 2, "page_size": 25}}), {"pagina": 2, "registros_por_pagina": 25})

    def test_project_and_category_parameters(self):
        self.assertEqual(build_params("projects.get", {"params": {"codInt": "PROJ-1"}}), {"codInt": "PROJ-1"})
        self.assertEqual(build_params("projects.list", {"params": {"page": 2, "nome_projeto": "Loja"}}), {"pagina": 2, "registros_por_pagina": 50, "nome_projeto": "Loja"})
        self.assertEqual(build_params("categories.create", {"body": {"categoria_superior": "1000", "descricao": "Receita de serviço", "tipo_categoria": "001"}}), {"categoria_superior": "1000", "descricao": "Receita de serviço", "tipo_categoria": "001"})
        self.assertEqual(build_params("category-groups.create", {"body": {"descricao": "Receitas", "tipo_grupo": "R"}}), {"descricao": "Receitas", "tipo_grupo": "R"})

    def test_transfer_builds_a_single_registered_transfer(self):
        data = build_params("account-transfers.create", {"body": {"integration_id": "TR-001", "source_account_id": 10, "destination_account_id": 20, "date": "19/08/2026", "amount": 50.5, "note": "Reserva"}})
        self.assertEqual(data["cabecalho"], {"nCodCC": 10, "dDtLanc": "19/08/2026", "nValorLanc": 50.5})
        self.assertEqual(data["transferencia"], {"nCodCCDestino": 20})
        self.assertEqual(data["detalhes"], {"cTipo": "TRA", "cObs": "Reserva"})

    def test_financial_title_and_payment_routing(self):
        title = build_params("payables.create", {"body": {"codigo_lancamento_integracao": "P-1", "codigo_cliente_fornecedor": 1, "data_vencimento": "20/08/2026", "valor_documento": 100, "codigo_categoria": "2.01", "data_previsao": "20/08/2026"}})
        self.assertEqual(title["codigo_lancamento_integracao"], "P-1")
        payment = build_params("receivables.receive", {"body": {"codigo_lancamento": 1, "codigo_conta_corrente": 2, "valor": 100, "data": "20/08/2026"}})
        self.assertEqual(payment["codigo_conta_corrente"], 2)
        allocation = build_params("receivables.department-allocation.create", {"body": {"chave_lancamento": 1, "distribuicao": [{"cCodDep": "D1", "nPerDep": 100}]}})
        self.assertEqual(allocation["chave_lancamento"], 1)

    def test_inbound_nfe_completion_uses_explicit_financial_fields(self):
        data = build_params("inbound-nfe-receipts.update-completed", {"body": {"receipt_id": 1, "purchase_category_code": "2.01", "bank_account_id": 2, "registration_date": "19/08/2026", "departments": [{"cCodDepartamento": "D1", "pDepartamento": 100}]}})
        self.assertEqual(data["ide"], {"nIdReceb": 1})
        self.assertEqual(data["infoAdicionais"]["cCategCompra"], "2.01")

    def test_invalid_identifiers_and_unregistered_fields_are_rejected(self):
        with self.assertRaises(OmieError):
            build_params("projects.get", {"params": {}})
        with self.assertRaises(OmieError):
            build_params("categories.update", {"body": {"codigo": "1001"}})
        with self.assertRaises(OmieError):
            build_params("projects.create", {"body": {"codInt": "P1", "nome": "Projeto", "extra": "x"}})

    @patch("omie.urllib.request.urlopen")
    def test_calls_the_registered_endpoint_and_preserves_response_fields(self, urlopen):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self): return json.dumps({"codigo": 1, "codInt": "P1", "campo_novo": "preservado"}).encode()
        urlopen.return_value = Response()
        data = call_api(load_settings(self.profile(), "laveli"), "key", "secret", ENDPOINTS["projects"], "ConsultarProjeto", {"codInt": "P1"})
        self.assertEqual(data["campo_novo"], "preservado")
        self.assertEqual(urlopen.call_args.args[0].full_url, ENDPOINTS["projects"])

    def test_read_does_not_require_confirmation(self):
        request = {"version": 1, "operation": "projects.get", "params": {"codInt": "P1"}}
        with patch("omie.load_settings", return_value=load_settings(self.profile(), "laveli")), patch("omie.credentials", return_value=("key", "secret")), patch("omie.call_api", return_value={"codigo": 1}) as call, patch("sys.argv", ["omie.py", "--config", "profile.ini", "--profile", "laveli"]), patch("sys.stdin", io.StringIO(json.dumps(request))), patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(main(), 0)
        self.assertEqual(call.call_args.args[4], "ConsultarProjeto")

    def test_writes_require_confirmation_at_wrapper_boundary(self):
        request = {"version": 1, "operation": "projects.delete", "params": {"codigo": 1}}
        with patch("sys.argv", ["omie.py", "--config", "profile.ini", "--profile", "laveli"]), patch("sys.stdin", io.StringIO(json.dumps(request))), patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main(), 1)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "confirmation_required")

    def test_rejects_unsupported_protocol_version(self):
        with patch("sys.argv", ["omie.py", "--config", "profile.ini", "--profile", "laveli"]), patch("sys.stdin", io.StringIO('{"version": 2, "operation": "projects.list"}')), patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main(), 1)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "unsupported_version")


if __name__ == "__main__":
    unittest.main()
