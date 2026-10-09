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

Use o ID retornado como `body.codigo_cliente_fornecedor` em contas a pagar/receber, `body.customer_id` em lançamentos diretos e `params.nIdFornecedor` na listagem de recebimentos de NF-e. Consulte [Contratos financeiros](references/financial-api-contracts.md) para os demais campos e a confirmação de escrita. As consultas não criam nem alteram cadastros. Para cadastrar por CNPJ, use `customers.create` conforme o contrato abaixo.


## Consultar tipos de documento

Use `document-types.list` para pesquisar o catálogo de tipos de documento dos títulos e `document-types.get` com `params.codigo` para conferir código e descrição. Consulte [Contratos da API](references/api-contracts.md#tipos-de-documento-dos-títulos) para validação, respostas e exemplos. O serviço não possui paginação. Não confunda esse catálogo com `document_type` dos lançamentos diretos.

Para trocar o tipo de um título existente, use `payables.update` com o código validado em `codigo_tipo_documento`, preservando os demais dados. Se o título estiver pago, não recrie o título, não cancele o pagamento nem registre outra baixa para realizar essa correção. Confira os dados e a situação após a atualização; respeite eventual restrição da Omie.


## Cadastrar contraparte por CNPJ

Use `customers.create` com `confirm:true`, `body.cnpj_cpf` e a razão social efetiva em `body.razao_social`. O wrapper consulta todos os cadastros antes de incluir, reutiliza um único cadastro ativo com o mesmo CNPJ e devolve `data.codigo_cliente_omie`. Interrompe se o cadastro estiver inativo, houver múltiplas correspondências ou a consulta falhar/estiver incompleta. Leia [Cadastro por CNPJ](references/api-contracts.md#cadastro-de-contraparte-por-cnpj) para limites, código de integração estável, resultado e recuperação de respostas incertas.

## Escolher o tipo de documento financeiro

Os tipos Outros (`99999`), Nota Fiscal Eletrônica (`NFE`), Fatura (`FAT`) e Nota Fiscal de Serviço (`NFS`) estão disponíveis no wrapper para contas a pagar/receber (`codigo_tipo_documento`) e lançamentos diretos (`document_type`, encaminhado como `detalhes.cTipo`). Preserve o tipo escolhido pelo usuário, sem substituição automática por `CRT` ou outro código. Em uma NFS-e, informe também `document_number` e a identificação complementar em `note`.

A Omie ainda deve aceitar o tipo no serviço solicitado; o catálogo de títulos não comprova aceitação em lançamentos diretos. Se houver rejeição do servidor, informe-a sem alterar silenciosamente o tipo ou criar outro fluxo financeiro. Consulte [Tipos de documento financeiros](references/financial-api-contracts.md#tipos-de-documento-financeiros) para formatos, limitações e verificações após o envio. Lançamento direto não cria conta a pagar nem solicita conciliação.
