# Contratos financeiros da API Omie

## Operações

| Grupo | Operações do wrapper |
|---|---|
| Contas correntes | `bank-accounts.list`, `bank-accounts.get` |
| Lançamentos diretos | `account-transactions.list`, `account-transactions.get`, `account-transactions.create`, `account-transactions.update`, `account-transactions.delete` |
| Transferências internas | `account-transfers.create` |
| Contas a pagar | `payables.list`, `payables.get`, `payables.create`, `payables.update`, `payables.upsert`, `payables.delete`, `payables.pay`, `payables.payment.cancel`, `payables.create-batch`, `payables.upsert-batch` |
| Contas a receber | `receivables.list`, `receivables.get`, `receivables.create`, `receivables.update`, `receivables.upsert`, `receivables.delete`, `receivables.receive`, `receivables.receipt.cancel`, `receivables.receipt.reconcile`, `receivables.receipt.unreconcile`, `receivables.department-allocation.create`, `receivables.department-allocation.update`, `receivables.department-allocation.delete`, `receivables.create-batch`, `receivables.upsert-batch` |
| Consulta consolidada | `financial-movements.list` |

## Transferência interna

`account-transfers.create` exige `integration_id`, `source_account_id`, `destination_account_id`, `date` no formato `dd/mm/aaaa` e `amount`. Os campos opcionais são `document_number`, `note`, `project_id` e `departments`.

O wrapper chama `IncluirLancCC` com `cabecalho.nCodCC` para a origem, `transferencia.nCodCCDestino` para o destino e `detalhes.cTipo` fixado como `TRA`. Ele rejeita contas de origem e destino iguais. Não simule a transferência com dois lançamentos independentes.

## Títulos e baixas

Para criar título a pagar ou a receber, envie ao menos `codigo_lancamento_integracao`, `codigo_cliente_fornecedor`, `data_vencimento`, `valor_documento` e `codigo_categoria`; `data_previsao` é recomendado para o fluxo financeiro. Para baixar um título, envie sua chave, `codigo_conta_corrente`, `valor` e `data`.

Todas as operações de escrita exigem `confirm: true`, incluindo baixas, cancelamentos, conciliações e operações em lote.

## Identificar o cliente ou fornecedor

Antes de lançar, localize o cadastro com `customers.list` por `params.clientesFiltro.cnpj_cpf`, razão social ou nome fantasia; consulte o detalhe com `customers.get` pelo identificador. O mesmo cadastro atende clientes e fornecedores. Use o `codigo_cliente_omie` retornado em `body.codigo_cliente_fornecedor` nos títulos (ou `body.titles[].codigo_cliente_fornecedor` nos lotes) e em `body.customer_id` nos lançamentos diretos, que o wrapper converte para `detalhes.nCodCliente`.

Veja [Clientes e fornecedores](api-contracts.md#clientes-e-fornecedores) para exemplos, busca por trecho de nome, paginação, verificação de documento e tratamento de candidatos ambíguos ou inativos. Uma falha de consulta não comprova inexistência; somente use o ID depois de validar o cadastro retornado.
