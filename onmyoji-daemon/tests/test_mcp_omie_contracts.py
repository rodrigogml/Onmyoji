"""Regressões dos contratos Omie migrados da integração legada para o MCP."""
import unittest

from onmyoji_daemon.mcp.omie.contracts import (
    DIRECT_DOCUMENT_TYPES, FINANCIAL_DOCUMENT_TYPES, build_params as mcp_build_params,
)
from onmyoji_daemon.mcp.omie.service import OPERATIONS as MCP_OPERATIONS, OmieError


def build_params(operation, arguments):
    return mcp_build_params(operation.replace("-", "_").replace(".", "_"), arguments)


OPERATIONS = {name.replace("-", "_").replace(".", "_"): value for name, value in MCP_OPERATIONS.items()}


class OmieContractRegressionTests(unittest.TestCase):
    def test_payable_barcode_preserved_in_titles_and_batches(self):
        body = {"codigo_lancamento_integracao": "TEST-BOLETO", "codigo_cliente_fornecedor": 1, "data_vencimento": "27/10/2026", "valor_documento": 440, "codigo_categoria": "2.11.97"}
        for barcode in ("00190.00009 01234.567890 12345.678901 1 10000000000100", "1" * 70):
            title = {**body, "codigo_barras_ficha_compensacao": barcode}
            for operation in ("payables.create", "payables.update", "payables.upsert"):
                with self.subTest(operation=operation, barcode=barcode):
                    self.assertEqual(build_params(operation, {"body": title}), title)
            for operation in ("payables.create-batch", "payables.upsert-batch"):
                self.assertEqual(build_params(operation, {"body": {"lote": 1, "titles": [title]}})["conta_pagar_cadastro"], [title])
        self.assertEqual(build_params("payables.create", {"body": body}), body)

    def test_payable_barcode_rejects_invalid_values_and_receivables(self):
        for barcode in (None, False, 123, "", " ", "1" * 71):
            title = {"codigo_barras_ficha_compensacao": barcode}
            for operation, request in (("payables.update", {"body": title}), ("payables.create-batch", {"body": {"lote": 1, "titles": [title]}})):
                with self.subTest(operation=operation, barcode=barcode), self.assertRaises(OmieError):
                    build_params(operation, request)
        with self.assertRaises(OmieError):
            build_params("receivables.update", {"body": {"codigo_barras_ficha_compensacao": "123"}})

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

    def test_customer_creation_accepts_cpf_and_preserves_leading_zero(self):
        for document in ("01234567890", "012.345.678-90"):
            with self.subTest(document=document):
                body = {"cnpj_cpf": document, "razao_social": "NOME COMPLETO"}
                result = build_params("customers.create", {"body": body})
                self.assertEqual(result, {"cnpj_cpf": "012.345.678-90", "razao_social": "NOME COMPLETO", "codigo_cliente_integracao": "CPF-01234567890"})
                body["codigo_cliente_integracao"] = "FORN-PESSOA-1"
                self.assertEqual(build_params("customers.create", {"body": body})["codigo_cliente_integracao"], "FORN-PESSOA-1")
        for document in (1234567890, "0123456789", "012345678901", "0123456789X", "012.345678-90", "012/345/678-90", " 01234567890", "01234567890\n", "０１２３４５６７８９０"):
            with self.subTest(document=document), self.assertRaises(OmieError) as error:
                build_params("customers.create", {"body": {"cnpj_cpf": document, "razao_social": "NOME COMPLETO"}})
            self.assertEqual(error.exception.code, "invalid_body")

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


    def test_native_department_amount_derives_percentage_before_http(self):
        body = {
            "integration_id": "TEST-VALOR-DEP", "account_id": 1, "date": "12/11/2026",
            "amount": 9599, "category_code": "2.01.03", "document_type": "99999",
            "departments": [{"cCodDep": "8532594895", "nValDep": 9599}],
        }
        result = build_params("account-transactions.create", {"body": body})
        self.assertEqual(result["departamentos"], [
            {"cCodDep": "8532594895", "nPerDep": 100.0, "nValDep": 9599.0},
        ])

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

    def test_payable_type_update_preserves_title_fields_without_payment(self):
        body = {
            "codigo_lancamento_omie": 9015289825, "codigo_tipo_documento": "FAT",
            "valor_documento": 100, "data_vencimento": "08/10/2026", "data_emissao": "01/10/2026",
            "data_previsao": "08/10/2026", "codigo_categoria": "2.01", "codigo_projeto": 123,
            "categorias": [{"codigo_categoria": "2.01", "percentual": 100}],
            "distribuicao": [{"codigo_departamento": "D1", "percentual": 100}],
        }
        self.assertEqual(build_params("payables.update", {"body": body}), body)
        self.assertEqual(OPERATIONS["payables_update"], ("payables", "AlterarContaPagar", True))

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

    def test_customer_id_is_reused_by_financial_operations(self):
        customer_id = 123
        for operation in ("payables.create", "receivables.create"):
            title = build_params(operation, {"body": {"codigo_lancamento_integracao": "T-1", "codigo_cliente_fornecedor": customer_id, "data_vencimento": "08/10/2026", "valor_documento": 100, "codigo_categoria": "2.01"}})
            self.assertEqual(title["codigo_cliente_fornecedor"], customer_id)
        transaction = build_params("account-transactions.create", {"body": {"integration_id": "L-1", "account_id": 1, "date": "08/10/2026", "amount": 100, "category_code": "2.01", "document_type": "CRT", "customer_id": customer_id}})
        self.assertEqual(transaction["detalhes"]["nCodCliente"], customer_id)

    def test_project_and_category_parameters(self):
        self.assertEqual(build_params("projects.get", {"params": {"codInt": "PROJ-1"}}), {"codInt": "PROJ-1"})
        self.assertEqual(build_params("projects.list", {"params": {"page": 2, "nome_projeto": "Loja"}}), {"pagina": 2, "registros_por_pagina": 50, "nome_projeto": "Loja"})
        self.assertEqual(build_params("categories.create", {"body": {"categoria_superior": "1000", "descricao": "Receita de serviço", "tipo_categoria": "001"}}), {"categoria_superior": "1000", "descricao": "Receita de serviço", "tipo_categoria": "001"})
        self.assertEqual(build_params("category-groups.create", {"body": {"descricao": "Receitas", "tipo_grupo": "R"}}), {"descricao": "Receitas", "tipo_grupo": "R"})

    def test_transfer_builds_a_single_registered_transfer(self):
        data = build_params("account-transfers.create", {"body": {"integration_id": "TR-001", "source_account_id": 10, "destination_account_id": 20, "date": "19/08/2026", "amount": 50.5, "category_code": "0.01.02", "note": "Reserva"}})
        self.assertEqual(data["cabecalho"], {"nCodCC": 10, "dDtLanc": "19/08/2026", "nValorLanc": 50.5})
        self.assertEqual(data["transferencia"], {"nCodCCDestino": 20})
        self.assertEqual(data["detalhes"], {"cCodCateg": "0.01.02", "cTipo": "TRA", "cObs": "Reserva"})

    def test_transfer_requires_a_category_code(self):
        with self.assertRaises(OmieError) as error:
            build_params("account-transfers.create", {"body": {"integration_id": "TR-001", "source_account_id": 10, "destination_account_id": 20, "date": "19/08/2026", "amount": 50.5}})
        self.assertEqual(error.exception.code, "invalid_body")

    def test_financial_title_and_payment_routing(self):
        title = build_params("payables.create", {"body": {"codigo_lancamento_integracao": "P-1", "codigo_cliente_fornecedor": 1, "data_vencimento": "20/08/2026", "valor_documento": 100, "codigo_categoria": "2.01", "data_previsao": "20/08/2026"}})
        self.assertEqual(title["codigo_lancamento_integracao"], "P-1")
        payment = build_params("receivables.receive", {"body": {"codigo_lancamento": 1, "codigo_conta_corrente": 2, "valor": 100, "data": "20/08/2026"}})
        self.assertEqual(payment["codigo_conta_corrente"], 2)
        allocation = build_params("receivables.department-allocation.create", {"body": {"chave_lancamento": 1, "distribuicao": [{"cCodDep": "D1", "nPerDep": 100}]}})
        self.assertEqual(allocation["chave_lancamento"], 1)

    def test_inbound_nfe_receipt_update_financial_data_and_allocation(self):
        body = {
            "receipt_id": 1, "purchase_category_code": "2.01.03",
            "bank_account_id": 8382785861, "project_id": 8444062614,
            "departments": [{"cCodDepartamento": "8532595062", "pDepartamento": 100, "vDepartamento": 2545.40}],
        }
        data = build_params("inbound-nfe-receipts.update", {"body": body})
        self.assertEqual(data, {
            "ide": {"nIdReceb": 1},
            "infoAdicionais": {"cCategCompra": "2.01.03", "nIdConta": 8382785861, "nIdProjeto": 8444062614},
            "departamentos": [{"cCodDepartamento": "8532595062", "pDepartamento": 100, "vDepartamento": 2545.40}],
        })
        self.assertNotIn("itensRecebimentoEditar", data)
        done = build_params("inbound-nfe-receipts.update-completed", {"body": {"receipt_id": 1, "project_id": 8444062614}})
        self.assertEqual(done, {"ide": {"nIdReceb": 1}, "infoAdicionais": {"nIdProjeto": 8444062614}})

    def test_receipt_native_and_semantic_aliases(self):
        expected = {
            "ide": {"nIdReceb": 1},
            "infoAdicionais": {"cCategCompra": "2.01.03", "nIdProjeto": 8444062614},
            "departamentos": [{"cCodDepartamento": "000123", "pDepartamento": 100, "vDepartamento": 2545.4}],
        }
        for body in (
            {"ide": {"nIdReceb": 1}, "infoAdicionais": {"cCategCompra": "2.01.03", "nIdProjeto": 8444062614},
             "departamentos": [{"cCodDepartamento": "000123", "pDepartamento": 100, "vDepartamento": 2545.4}]},
            {"receipt_id": 1, "additional_info": {"purchase_category_code": "2.01.03", "project_id": 8444062614},
             "department_allocations": [{"department_code": "000123", "percentage": 100, "amount": 2545.4}]},
        ):
            with self.subTest(body=body):
                self.assertEqual(build_params("inbound-nfe-receipts.update", {"body": body}), expected)
        self.assertEqual(build_params("inbound-nfe-receipts.update-completed", {"body": {
            "ide": {"nIdReceb": 1}, "additional_info": {"project_id": 8444062614}
        }}), {"ide": {"nIdReceb": 1}, "infoAdicionais": {"nIdProjeto": 8444062614}})

    def test_receipt_aliases_reject_conflicts_and_invalid_values(self):
        invalid = [
            {"receipt_id": 1, "ide": {"nIdReceb": 1}, "project_id": 2},
            {"receipt_id": 1, "project_id": 3, "additional_info": {"project_id": 2}},
            {"receipt_id": 1, "infoAdicionais": {"nIdFornecedor": 2}},
            {"receipt_id": 1, "additional_info": {"project_id": "STRA36"}},
            {"receipt_id": 1, "department_allocations": [{"department_code": "D1", "amount": 2545.4}]},
            {"receipt_id": 1, "departamentos": []},
            {"receipt_id": 1, "departments": [{"cCodDepartamento": "D1", "pDepartamento": 100}],
             "department_allocations": [{"department_code": "D2", "percentage": 100}]},
        ]
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(OmieError) as error:
                build_params("inbound-nfe-receipts.update", {"body": body})
            self.assertEqual(error.exception.code, "invalid_body")
        for alias in ("department_allocations", "departamentos"):
            with self.assertRaises(OmieError) as error:
                build_params("inbound-nfe-receipts.update-completed", {"body": {
                    "receipt_id": 1, alias: [{"cCodDepartamento": "D1", "pDepartamento": 100}]
                }})
            self.assertEqual(error.exception.code, "invalid_body")

    def test_inbound_nfe_receipt_rejects_unmapped_fields_or_empty_changes(self):
        invalid = [
            ("update", {"receipt_id": 1}),
            ("update", {"receipt_id": 1, "items": {}}),
            ("update", {"receipt_id": 1, "purchase_category_code": ""}),
            ("update", {"receipt_id": 1, "project_id": "8444062614"}),
            ("update", {"receipt_id": 1, "supplier_id": 9028181622, "purchase_category_code": "2.01.03"}),
            ("update", {"receipt_id": 1, "total": 2545.40}),
            ("update", {"receipt_id": 1, "departments": [{"cCodDepartamento": 8532595062, "pDepartamento": 100}]}),
            ("update", {"receipt_id": 1, "departments": [{"cCodDepartamento": "D1", "pDepartamento": 60}]}),
            ("update", {"receipt_id": 1, "departments": [{"cCodDepartamento": "D1", "pDepartamento": 100, "vDepartamento": -2}]}),
            ("update-completed", {"receipt_id": 1, "departments": [{"cCodDepartamento": "D1", "pDepartamento": 100}]}),
            ("update-completed", {"receipt_id": 1}),
            ("update-completed", {"receipt_id": 1, "categories": []}),
            ("update-completed", {"receipt_id": 1, "registration_date": "31/02/2026"}),
        ]
        for operation, body in invalid:
            with self.subTest(operation=operation, body=body), self.assertRaises(OmieError) as error:
                build_params("inbound-nfe-receipts." + operation, {"body": body})
            self.assertEqual(error.exception.code, "invalid_body")

    def test_inbound_nfe_receipt_accepts_item_when_explicit_and_valid(self):
        payload = {"body": {"receipt_id": 1, "items": [{"itensIde": {"nSequencia": 1, "cAcao": "IGNORAR"}}]}}
        result = build_params("inbound-nfe-receipts.update", payload)
        self.assertEqual(result["itensRecebimentoEditar"], [{"itensIde": {"nSequencia": 1, "cAcao": "IGNORAR"}}])

    def test_invalid_identifiers_and_unregistered_fields_are_rejected(self):
        with self.assertRaises(OmieError):
            build_params("projects.get", {"params": {}})
        with self.assertRaises(OmieError):
            build_params("categories.update", {"body": {"codigo": "1001"}})
        with self.assertRaises(OmieError):
            build_params("projects.create", {"body": {"codInt": "P1", "nome": "Projeto", "extra": "x"}})
