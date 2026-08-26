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
