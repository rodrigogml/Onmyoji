import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from omie import OPERATIONS, ENDPOINTS, DIRECT_DOCUMENT_TYPES, FINANCIAL_DOCUMENT_TYPES, OmieError, build_params, call_api, create_customer, load_settings, main


class OmieTests(unittest.TestCase):
    def test_customer_creation_validates_minimal_contract(self):
        for cnpj in ("52438909000120", "52.438.909/0001-20"):
            result = build_params("customers.create", {"body": {"cnpj_cpf": cnpj, "razao_social": "TELL"}})
            self.assertEqual(result, {"cnpj_cpf": "52.438.909/0001-20", "razao_social": "TELL", "codigo_cliente_integracao": "CNPJ-52438909000120"})
        body = {"cnpj_cpf": "52438909000120", "razao_social": "R" * 60, "codigo_cliente_integracao": "I" * 60}
        self.assertEqual(build_params("customers.create", {"body": body})["codigo_cliente_integracao"], "I" * 60)
        invalid = [
            {"body": value} for value in (None, [], False, "TELL")
        ] + [
            {"body": {"cnpj_cpf": value, "razao_social": "TELL"}}
            for value in (None, 52438909000120, True, "", "123", "5243890900012X", "52/438909000120", "５２４３８９０９０００１２０")
        ] + [
            {"body": {"cnpj_cpf": "52438909000120", "razao_social": value}}
            for value in (None, "", " ", 1, "R" * 61)
        ] + [
            {"body": {**body, "codigo_cliente_integracao": value}} for value in (None, "", "I" * 61)
        ] + [{"body": {**body, "codigo_cliente_omie": 1}}, {"body": body, "params": {"method": "UpsertCliente"}}, {"body": body, "params": None}]
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(OmieError):
                build_params("customers.create", request)

    def customer_page(self, rows, page=1, pages=1, total=None):
        return {"pagina": page, "total_de_paginas": pages, "total_de_registros": len(rows) if total is None else total, "clientes_cadastro": rows}

    def customer_body(self):
        return build_params("customers.create", {"body": {"cnpj_cpf": "52438909000120", "razao_social": "TELL"}})

    @patch("omie.call_api")
    def test_create_customer_finds_existing_cnpj_on_second_page(self, call):
        rows = [{"codigo_cliente_omie": i, "cnpj_cpf": "", "inativo": "N"} for i in range(1, 51)]
        match = {"codigo_cliente_omie": 57, "cnpj_cpf": "52.438.909/0001-20", "razao_social": "TELL LTDA", "inativo": "N"}
        call.side_effect = [self.customer_page(rows, pages=2, total=51), self.customer_page([match], page=2, pages=2, total=51)]
        result = create_customer(load_settings(self.profile(), "laveli"), "key", "secret", self.customer_body())
        self.assertEqual(result["codigo_cliente_omie"], 57)
        self.assertFalse(result["created"])
        self.assertEqual(result["customer"], match)
        self.assertEqual(call.call_count, 2)
        for page, invocation in enumerate(call.call_args_list, 1):
            self.assertEqual(invocation.args[4], "ListarClientes")
            self.assertEqual(invocation.args[5], {"pagina": page, "registros_por_pagina": 50, "apenas_importado_api": "N"})

    @patch("omie.call_api")
    def test_create_customer_includes_only_after_complete_absence(self, call):
        call.side_effect = [self.customer_page([], pages=0), {"codigo_cliente_omie": 123, "codigo_status": "0"}]
        settings = load_settings(self.profile(), "laveli")
        result = create_customer(settings, "key", "secret", self.customer_body())
        self.assertEqual(result, {"codigo_cliente_omie": 123, "codigo_status": "0", "created": True})
        self.assertEqual(call.call_args.args[4], "IncluirCliente")
        self.assertEqual(call.call_args.args[5], self.customer_body())
        self.assertEqual(call.call_args.args[0].retries, 0)
        self.assertEqual(settings.retries, 2)

    @patch("omie.call_api")
    def test_customer_duplicates_inactive_and_incomplete_reads_prevent_write(self, call):
        match = {"codigo_cliente_omie": 1, "cnpj_cpf": "52438909000120", "inativo": "N"}
        cases = (
            ([self.customer_page([match, {**match, "codigo_cliente_omie": 2}])], "ambiguous_customer"),
            ([self.customer_page([{**match, "inativo": "S"}])], "customer_inactive"),
            ([self.customer_page([{**match, "inativo": None}])], "invalid_response"),
            ([{}], "invalid_response"),
            ([self.customer_page([match], total=2)], "invalid_response"),
            ([self.customer_page([match, match])], "invalid_response"),
            ([self.customer_page([], pages=2, total=1)], "invalid_response"),
            ([self.customer_page([match], pages=2, total=2), self.customer_page([{**match, "codigo_cliente_omie": 2}], page=2, pages=2, total=3)], "invalid_response"),
            ([OmieError("network_error", "secret")], "network_error"),
        )
        for responses, code in cases:
            call.reset_mock()
            call.side_effect = responses
            with self.subTest(code=code), self.assertRaises(OmieError) as error:
                create_customer(load_settings(self.profile(), "laveli"), "key", "secret", self.customer_body())
            self.assertEqual(error.exception.code, code)
            self.assertTrue(all(invocation.args[4] == "ListarClientes" for invocation in call.call_args_list))

    @patch("omie.call_api")
    def test_customer_creation_rejects_business_failure_and_missing_id(self, call):
        for response, code in (({"codigo_status": "1", "descricao_status": "secret"}, "omie_api_error"), ({"codigo_status": "0"}, "invalid_response"), ({"codigo_status": "0", "codigo_cliente_omie": True}, "invalid_response")):
            call.side_effect = [self.customer_page([]), response]
            with self.subTest(response=response), self.assertRaises(OmieError) as error:
                create_customer(load_settings(self.profile(), "laveli"), "key", "secret", self.customer_body())
            self.assertEqual(error.exception.code, code)
            self.assertNotIn("secret", error.exception.message)

    def test_customer_creation_confirmation_and_cli_routing(self):
        request = {"version": 1, "operation": "customers.create", "body": {"cnpj_cpf": "52438909000120", "razao_social": "TELL"}}
        with patch("omie.load_settings", return_value=load_settings(self.profile(), "laveli")), patch("omie.credentials", return_value=("key", "secret")) as credentials, patch("omie.create_customer", return_value={"codigo_cliente_omie": 123, "created": True}) as create, patch("sys.argv", ["omie.py", "--config", "profile.ini", "--profile", "laveli"]), patch("sys.stdout", new_callable=io.StringIO) as output:
            with patch("sys.stdin", io.StringIO(json.dumps(request))):
                self.assertEqual(main(), 1)
            self.assertEqual(json.loads(output.getvalue())["error"]["code"], "confirmation_required")
            credentials.assert_not_called()
            create.assert_not_called()
            output.seek(0)
            output.truncate()
            with patch("sys.stdin", io.StringIO(json.dumps({**request, "confirm": True}))):
                self.assertEqual(main(), 0)
            self.assertEqual(json.loads(output.getvalue())["data"]["codigo_cliente_omie"], 123)
            self.assertEqual(create.call_args.args[3], self.customer_body())

    def test_direct_transaction_types_and_note_identity(self):
        body = {"integration_id": "TELL-NFSE-25", "id": 1, "account_id": 2, "date": "08/10/2026", "amount": 1280, "category_code": "2.01", "document_number": "25", "customer_id": 3, "note": "NFS-e 25 TELL CNPJ 52.438.909/0001-20"}
        for operation in ("account-transactions.create", "account-transactions.update"):
            for document_type in DIRECT_DOCUMENT_TYPES:
                with self.subTest(operation=operation, document_type=document_type):
                    result = build_params(operation, {"body": {**body, "document_type": document_type}})
                    self.assertEqual(result["detalhes"]["cTipo"], document_type)
                    self.assertEqual(result["detalhes"]["cNumDoc"], "25")
                    self.assertEqual(result["detalhes"]["nCodCliente"], 3)
                    self.assertEqual(result["detalhes"]["cObs"], body["note"])
                    self.assertEqual(set(result), {"cCodIntLanc", "nCodLanc", "cabecalho", "detalhes"})
            for value in ("PAG", "crt", "", None, True, [], {}):
                with self.subTest(operation=operation, value=value), self.assertRaises(OmieError) as error:
                    build_params(operation, {"body": {**body, "document_type": value}})
                self.assertEqual(error.exception.code, "invalid_body")
            for value in (None, 25, "", "x" * 21):
                with self.subTest(operation=operation, value=value), self.assertRaises(OmieError):
                    build_params(operation, {"body": {**body, "document_type": "CRT", "document_number": value}})

    def test_four_document_types_are_preserved_in_all_financial_operations(self):
        self.assertEqual(FINANCIAL_DOCUMENT_TYPES, {"99999", "NFE", "FAT", "NFS"})
        title = {"codigo_lancamento_omie": 1, "codigo_lancamento_integracao": "NFS-25", "codigo_cliente_fornecedor": 2, "data_vencimento": "08/10/2026", "valor_documento": 1280, "codigo_categoria": "2.01", "codigo_projeto": 3, "distribuicao": []}
        for kind in ("payables", "receivables"):
            for action in ("create", "update", "upsert"):
                for code in FINANCIAL_DOCUMENT_TYPES:
                    with self.subTest(kind=kind, action=action, code=code):
                        body = {**title, "codigo_tipo_documento": code}
                        self.assertEqual(build_params(f"{kind}.{action}", {"body": body}), body)
        transaction = {"integration_id": "TELL-NFSE-25", "id": 1, "account_id": 2, "date": "08/10/2026", "amount": 1280, "category_code": "2.01", "document_number": "25"}
        for action in ("create", "update"):
            for code in FINANCIAL_DOCUMENT_TYPES:
                with self.subTest(action=action, code=code):
                    result = build_params(f"account-transactions.{action}", {"body": {**transaction, "document_type": code}})
                    self.assertEqual(result["detalhes"]["cTipo"], code)
                    self.assertEqual(result["detalhes"]["cNumDoc"], "25")
                    self.assertNotIn("diversos", result)

    @patch("omie.urllib.request.urlopen")
    def test_direct_transaction_processing_failure_is_not_reported_as_success(self, urlopen):
        settings = load_settings(self.profile(), "laveli")
        for status in ("1", 2, None, True, []):
            urlopen.return_value.__enter__.return_value.read.return_value = json.dumps({"cCodStatus": status, "cDesStatus": "secret"}).encode()
            with self.subTest(status=status), self.assertRaises(OmieError) as error:
                call_api(settings, "key", "secret", ENDPOINTS["account_transactions"], "IncluirLancCC", {"detalhes": {"cTipo": "NFS"}})
            self.assertEqual(error.exception.code, "omie_api_error")
            self.assertNotIn("secret", error.exception.message)
        for status in ("0", 0):
            response = {"cCodStatus": status, "nCodLanc": 1}
            urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(response).encode()
            self.assertEqual(call_api(settings, "key", "secret", ENDPOINTS["account_transactions"], "IncluirLancCC", {}), response)

    def test_document_catalog_parameters(self):
        for request in ({}, {"params": {}}, {"params": {"codigo": ""}}, {"params": {"codigo": "99999"}}):
            with self.subTest(request=request):
                self.assertEqual(build_params("document-types.list", request), {"codigo": request.get("params", {}).get("codigo", "")})
        self.assertEqual(build_params("document-types.get", {"params": {"codigo": "FAT"}}), {"codigo": "FAT"})

    def test_document_catalog_rejects_invalid_parameters(self):
        invalid = [
            {"params": {"codigo": value}} for value in (None, True, 123, [], {}, "123456", " ")
        ] + [
            {"params": value} for value in (None, False, [], "FAT")
        ] + [
            {"params": {"page": 1}}, {"params": {"descricao": "Fatura"}},
            {"params": {"endpoint": "https://example.org"}},
            {"params": {"method": "ConsultarTipoDocumento"}},
            {"body": {"codigo": "FAT"}}, {"body": None}, {"body": []},
        ]
        for operation in ("document-types.list", "document-types.get"):
            for request in invalid:
                with self.subTest(operation=operation, request=request), self.assertRaises(OmieError) as error:
                    build_params(operation, request)
                self.assertEqual(error.exception.code, "invalid_request")
        for request in ({}, {"params": {}}, {"params": {"codigo": ""}}):
            with self.subTest(request=request), self.assertRaises(OmieError) as error:
                build_params("document-types.get", request)
            self.assertEqual(error.exception.code, "missing_parameter")

    def test_document_catalog_cli_routes_reads_and_preserves_native_response(self):
        cases = (
            ("document-types.list", {}, "PesquisarTipoDocumento", {"tipo_documento_cadastro": [{"codigo": "FAT", "descricao": "Fatura"}]}),
            ("document-types.get", {"codigo": "FAT"}, "ConsultarTipoDocumento", {"codigo": "FAT", "descricao": "Fatura"}),
        )
        for operation, params, method, response in cases:
            request = {"version": 1, "operation": operation, "params": params}
            with self.subTest(operation=operation), patch("omie.load_settings", return_value=load_settings(self.profile(), "laveli")), patch("omie.credentials", return_value=("key", "secret")) as credentials, patch("omie.call_api", return_value=response) as call, patch("sys.argv", ["omie.py", "--config", "profile.ini", "--profile", "laveli"]), patch("sys.stdin", io.StringIO(json.dumps(request))), patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(main(), 0)
                self.assertEqual(json.loads(output.getvalue())["data"], response)
                self.assertEqual(call.call_args.args[3:5], ("https://app.omie.com.br/api/v1/geral/tiposdoc/", method))
                self.assertEqual(call.call_args.args[5], {"codigo": params.get("codigo", "")})
                credentials.assert_called_once()
                self.assertFalse(OPERATIONS[operation][2])

    def test_invalid_document_catalog_request_does_not_read_credentials(self):
        request = {"version": 1, "operation": "document-types.get", "params": {"codigo": 1}}
        with patch("omie.load_settings", return_value=load_settings(self.profile(), "laveli")), patch("omie.credentials") as credentials, patch("omie.call_api") as call, patch("sys.argv", ["omie.py", "--config", "profile.ini", "--profile", "laveli"]), patch("sys.stdin", io.StringIO(json.dumps(request))), patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main(), 1)
            self.assertEqual(json.loads(output.getvalue())["error"]["code"], "invalid_request")
            credentials.assert_not_called()
            call.assert_not_called()

    @patch("omie.urllib.request.urlopen")
    def test_document_catalog_api_errors_are_sanitized(self, urlopen):
        settings = load_settings(self.profile(), "laveli")
        failures = (
            (json.dumps({"faultcode": "SOAP-ENV:Client", "faultstring": "secret"}).encode(), "omie_api_error"),
            (b"not json secret", "invalid_response"),
        )
        for payload, code in failures:
            urlopen.return_value.__enter__.return_value.read.return_value = payload
            with self.subTest(code=code), self.assertRaises(OmieError) as error:
                call_api(settings, "key", "secret", ENDPOINTS["document_types"], "ConsultarTipoDocumento", {"codigo": "FAT"})
            self.assertEqual(error.exception.code, code)
            self.assertNotIn("secret", error.exception.message)
        urlopen.side_effect = urllib.error.HTTPError(ENDPOINTS["document_types"], 403, "secret", {}, None)
        with self.assertRaises(OmieError) as error:
            call_api(settings, "key", "secret", ENDPOINTS["document_types"], "ConsultarTipoDocumento", {"codigo": "FAT"})
        self.assertEqual(error.exception.code, "omie_http_error")
        self.assertEqual(error.exception.status, 403)
        self.assertNotIn("secret", error.exception.message)

    def test_payable_type_update_preserves_title_fields_without_payment(self):
        body = {
            "codigo_lancamento_omie": 9015289825, "codigo_tipo_documento": "FAT",
            "valor_documento": 100, "data_vencimento": "08/10/2026", "data_emissao": "01/10/2026",
            "data_previsao": "08/10/2026", "codigo_categoria": "2.01", "codigo_projeto": 123,
            "categorias": [{"codigo_categoria": "2.01", "percentual": 100}],
            "distribuicao": [{"codigo_departamento": "D1", "percentual": 100}],
        }
        self.assertEqual(build_params("payables.update", {"body": body}), body)
        self.assertEqual(OPERATIONS["payables.update"], ("payables", "AlterarContaPagar", True))

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

    def test_customer_list_filters_and_pagination(self):
        for field, value in (("cnpj_cpf", "18.511.742/0001-47"), ("razao_social", "Omie"), ("nome_fantasia", "Omie"), ("codigo_cliente_omie", 123), ("codigo_cliente_integracao", "F-1"), ("inativo", "N"), ("tags", [{"tag": "Fornecedor"}])):
            with self.subTest(field=field):
                filters = {field: value}
                self.assertEqual(build_params("customers.list", {"params": {"page": 2, "page_size": 10, "clientesFiltro": filters}}), {"pagina": 2, "registros_por_pagina": 10, "clientesFiltro": filters})
        self.assertEqual(build_params("customers.list", {}), {"pagina": 1, "registros_por_pagina": 50})
        self.assertEqual(build_params("customers.list", {"params": {"pagina": 3, "registros_por_pagina": 1, "apenas_importado_api": "N", "exibir_obs": "S", "filtrar_por_data_de": "01/10/2026"}}), {"pagina": 3, "registros_por_pagina": 1, "apenas_importado_api": "N", "exibir_obs": "S", "filtrar_por_data_de": "01/10/2026"})

    def test_customer_filter_accepts_documented_length_boundaries(self):
        for field, limit in (("codigo_cliente_integracao", 60), ("cnpj_cpf", 20), ("razao_social", 60), ("nome_fantasia", 100)):
            with self.subTest(field=field):
                filters = {field: "x" * limit}
                self.assertEqual(build_params("customers.list", {"params": {"clientesFiltro": filters}})["clientesFiltro"], filters)

    def test_invented_supplier_operation_fails_without_loading_credentials(self):
        for operation in ("suppliers.list", "ListarClientes", "ConsultarFornecedor"):
            with self.subTest(operation=operation), patch("omie.load_settings") as settings, patch("omie.credentials") as vault, patch("sys.argv", ["omie.py", "--config", "profile.toml", "--profile", "laveli"]), patch("sys.stdin", io.StringIO(json.dumps({"version": 1, "operation": operation}))), patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(main(), 1)
                self.assertEqual(json.loads(output.getvalue())["error"]["code"], "unsupported_operation")
                settings.assert_not_called()
                vault.assert_not_called()

    def test_customer_get_accepts_only_registered_identifiers(self):
        for params in ({"codigo_cliente_omie": 123}, {"codigo_cliente_integracao": "F-1"}, {"codigo_cliente_omie": 123, "codigo_cliente_integracao": "F-1"}):
            with self.subTest(params=params):
                self.assertEqual(build_params("customers.get", {"params": params}), params)
        with self.assertRaises(OmieError) as error:
            build_params("customers.get", {})
        self.assertEqual(error.exception.code, "missing_parameter")

    def test_customer_invalid_filters_fail_before_api(self):
        invalid_filters = [None, [], "Omie", {"extra": "x"}]
        invalid_filters += [{"codigo_cliente_omie": value} for value in (None, True, 0, -1, 1.5, "123")]
        invalid_filters += [{field: value} for field, limit in (("codigo_cliente_integracao", 60), ("cnpj_cpf", 20), ("razao_social", 60), ("nome_fantasia", 100)) for value in (None, 123, "", " ", "x" * (limit + 1))]
        invalid_filters += [{"inativo": value} for value in (None, [], "X")]
        invalid_filters += [{"tags": value} for value in (None, {}, [], [None], [{"tag": None}], [{"tag": ""}], [{"tag": " "}], [{"tag": "Fornecedor", "extra": "x"}])]
        for filters in invalid_filters:
            with self.subTest(filters=filters), self.assertRaises(OmieError) as error:
                build_params("customers.list", {"params": {"clientesFiltro": filters}})
            self.assertEqual(error.exception.code, "invalid_request")
        for params in ({"cnpj_cpf": "18511742000147"}, {"supplier_id": 1}, {"codigo_cliente_omie": False}, {"codigo_cliente_integracao": None}):
            with self.subTest(params=params), self.assertRaises(OmieError):
                build_params("customers.get", {"params": params})

    def test_customer_pagination_flags_and_dates_reject_invalid_values(self):
        for field in ("page", "page_size", "pagina", "registros_por_pagina"):
            for value in (None, False, "2", 0, -1, 1.5):
                with self.subTest(field=field, value=value), self.assertRaises(OmieError) as error:
                    build_params("customers.list", {"params": {field: value}})
                self.assertEqual(error.exception.code, "invalid_request")
        for params in ({"page_size": 51}, {"apenas_importado_api": None}, {"exibir_obs": "X"}, {"filtrar_por_data_de": 1}, {"filtrar_por_hora_de": " "}, {"cnpj_cpf": "18511742000147"}):
            with self.subTest(params=params), self.assertRaises(OmieError):
                build_params("customers.list", {"params": params})

    @patch("omie.urllib.request.urlopen")
    def test_customer_cli_sends_registered_method_and_preserves_ids(self, urlopen):
        class Response:
            def __init__(self, data): self.data = data
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self): return json.dumps(self.data).encode()
        customer = {"codigo_cliente_omie": 123, "cnpj_cpf": "18.511.742/0001-47", "razao_social": "Omie", "tags": [{"tag": "Fornecedor"}], "inativo": "N"}
        for operation, params, data, method in (
            ("customers.list", {"clientesFiltro": {"cnpj_cpf": customer["cnpj_cpf"]}}, {"clientes_cadastro": [customer], "pagina": 1, "total_de_paginas": 2}, "ListarClientes"),
            ("customers.get", {"codigo_cliente_omie": 123}, customer, "ConsultarCliente"),
        ):
            with self.subTest(operation=operation):
                urlopen.return_value = Response(data)
                request = {"version": 1, "operation": operation, "params": params}
                with patch("omie.load_settings", return_value=load_settings(self.profile(), "laveli")), patch("omie.credentials", return_value=("key", "secret")), patch("sys.argv", ["omie.py", "--config", "profile.toml", "--profile", "laveli"]), patch("sys.stdin", io.StringIO(json.dumps(request))), patch("sys.stdout", new_callable=io.StringIO) as output:
                    self.assertEqual(main(), 0)
                response = json.loads(output.getvalue())
                self.assertEqual(response["data"], data)
                http_request = urlopen.call_args.args[0]
                self.assertEqual(http_request.full_url, "https://app.omie.com.br/api/v1/geral/clientes/")
                self.assertEqual(json.loads(http_request.data), {"call": method, "app_key": "key", "app_secret": "secret", "param": [build_params(operation, request)]})
                self.assertFalse(OPERATIONS[operation][2])

    def test_customer_id_is_reused_by_financial_operations(self):
        customer_id = 123
        for operation in ("payables.create", "receivables.create"):
            title = build_params(operation, {"body": {"codigo_lancamento_integracao": "T-1", "codigo_cliente_fornecedor": customer_id, "data_vencimento": "08/10/2026", "valor_documento": 100, "codigo_categoria": "2.01"}})
            self.assertEqual(title["codigo_cliente_fornecedor"], customer_id)
        transaction = build_params("account-transactions.create", {"body": {"integration_id": "L-1", "account_id": 1, "date": "08/10/2026", "amount": 100, "category_code": "2.01", "document_type": "CRT", "customer_id": customer_id}})
        self.assertEqual(transaction["detalhes"]["nCodCliente"], customer_id)

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
