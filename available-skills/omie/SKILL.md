---
name: omie
description: Use para consultar e administrar cadastros, NF-e, contas correntes, transferências, contas a pagar e receber da Omie por perfis seguros do Onmyōji. Acione quando a tarefa precisar operar a API Omie usando app_key e app_secret recuperados exclusivamente do KeePass Vault.
---

# Omie

Neste documento, `<CODEX_HOME>` é um placeholder para o diretório raiz da instância atual do Shikigami. Não o digite literalmente nem o substitua pelo workspace; use o diretório efetivamente configurado como `CODEX_HOME` para essa instância.

Use `scripts/omie.py --config <CODEX_HOME>/configs/omie.toml --profile <perfil>` e envie uma requisição JSON `version: 1` pelo stdin. O conjunto de operações registrado é fechado; não informe URLs ou métodos arbitrários.

Configure com `setupSkill.py`. A credencial é lida de uma entrada KeePass usando o perfil configurado: `app_key` e `app_secret` normalmente estão nos campos `username` e `password`. Confirme toda escrita antes de enviar `confirm: true`. Consulte as referências desta skill para os formatos de cada operação.

## Consultar clientes e fornecedores

Leia [Contratos da API](references/api-contracts.md#clientes-e-fornecedores) antes de localizar um cadastro. `customers.list` chama `ListarClientes`, com CNPJ/CPF, razão social ou nome fantasia em `params.clientesFiltro` (objeto JSON); `customers.get` chama `ConsultarCliente`, com `codigo_cliente_omie` ou `codigo_cliente_integracao`. Ambas são leituras e consultam o cadastro compartilhado de clientes e fornecedores.

Não invente operações como `suppliers.list`, `ConsultarFornecedor` ou `ListarFornecedores`: elas não estão registradas no wrapper. Para CNPJ, use a listagem filtrada; a consulta individual aceita somente IDs. Percorra `data.total_de_paginas` e valide o CNPJ retornado antes de escolher `codigo_cliente_omie`. Para parte de nome, a referência explica a busca local paginada, sem presumir suporte a curingas pela API.

Use o ID retornado como `body.codigo_cliente_fornecedor` em contas a pagar/receber, `body.customer_id` em lançamentos diretos e `params.nIdFornecedor` na listagem de recebimentos de NF-e. Consulte [Contratos financeiros](references/financial-api-contracts.md) para os demais campos e a confirmação de escrita. A consulta não cria nem altera cadastros; inclusão de clientes/fornecedores ainda não é uma operação disponível neste wrapper.
