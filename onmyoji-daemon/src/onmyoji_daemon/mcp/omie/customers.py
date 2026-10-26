"""Criação idempotente de contraparte por CPF/CNPJ na Omie."""
from __future__ import annotations

import re
from typing import Any, Callable, Mapping

from .contracts import fail


def create_customer(body: Mapping[str, Any], request: Callable[..., Any]) -> dict[str, Any]:
    """Reuse a unique active CPF/CNPJ before IncluirCliente; never update a match.

    Scan every unfiltered page, including inactive/non-API customers. Raise
    invalid_response on incomplete/inconsistent results, ambiguous_customer on
    multiple document matches, customer_inactive on an inactive match and
    omie_api_error on a nonzero inclusion status. Network/API failures propagate.
    Inclusion has no automatic retries; a stable integration ID helps reconcile
    uncertain outcomes, but this read-before-write is not a transactional lock.
    """
    wanted = re.sub(r"[^0-9]", "", body["cnpj_cpf"])
    matches: list[Mapping[str, Any]] = []
    seen: set[int] = set()
    page, totals = 1, None
    while True:
        response = request("ListarClientes", {"pagina": page, "registros_por_pagina": 50, "apenas_importado_api": "N"})
        if not isinstance(response, Mapping):
            fail("invalid_response", "Consulta prévia de clientes incompleta.")
        rows = response.get("clientes_cadastro")
        pages, count = response.get("total_de_paginas"), response.get("total_de_registros")
        if not isinstance(rows, list) or type(pages) is not int or pages < 0 or type(count) is not int or count < 0 or type(response.get("pagina")) is not int or response["pagina"] != page or len(rows) > 50:
            fail("invalid_response", "Paginação de clientes inválida; inclusão não executada.")
        if (count > 0 and pages != (count + 49) // 50) or (count == 0 and pages not in (0, 1)):
            fail("invalid_response", "Totais da consulta prévia de clientes são inconsistentes.")
        if totals is None:
            totals = (pages, count)
        if totals != (pages, count) or (not rows and count > 0):
            fail("invalid_response", "Consulta de clientes mudou ou está incompleta; repita a consulta.")
        for row in rows:
            if not isinstance(row, Mapping) or type(row.get("codigo_cliente_omie")) is not int or row["codigo_cliente_omie"] < 1 or row["codigo_cliente_omie"] in seen or not isinstance(row.get("cnpj_cpf"), str):
                fail("invalid_response", "Cadastro retornado na consulta prévia é inválido.")
            seen.add(row["codigo_cliente_omie"])
            if re.sub(r"[^0-9]", "", row["cnpj_cpf"]) == wanted:
                matches.append(row)
        if page >= pages:
            break
        page += 1
    if len(seen) != totals[1]:
        fail("invalid_response", "Consulta prévia não recuperou todos os clientes.")
    if len(matches) > 1:
        fail("ambiguous_customer", "Mais de um cadastro possui o CPF/CNPJ; resolva a duplicidade antes de usar o ID.")
    if matches:
        match = matches[0]
        if match.get("inativo") not in ("S", "N"):
            fail("invalid_response", "Situação do cadastro existente não foi informada.")
        if match["inativo"] == "S":
            fail("customer_inactive", "O CPF/CNPJ já existe em cadastro inativo; não foi criada duplicata.")
        return {"codigo_cliente_omie": match["codigo_cliente_omie"], "created": False, "customer": dict(match)}
    response = request("IncluirCliente", body, retries=0)
    if not isinstance(response, Mapping) or type(response.get("codigo_status")) not in (str, int) or response["codigo_status"] not in ("0", 0):
        fail("omie_api_error", "A inclusão do cliente não retornou status de sucesso.")
    if type(response.get("codigo_cliente_omie")) is not int or response["codigo_cliente_omie"] < 1:
        fail("invalid_response", "A inclusão não retornou codigo_cliente_omie válido; consulte antes de repetir.")
    return {**response, "created": True}
