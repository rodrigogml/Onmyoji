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


## Tipos de documento financeiros

O wrapper disponibiliza os quatro tipos de documento solicitados nos dois fluxos:

| Tipo | Código | Contas a pagar/receber | Lançamentos diretos |
|---|---|---|---|
| Outros | `99999` | `body.codigo_tipo_documento` | `body.document_type` |
| Nota Fiscal Eletrônica | `NFE` | `body.codigo_tipo_documento` | `body.document_type` |
| Fatura | `FAT` | `body.codigo_tipo_documento` | `body.document_type` |
| Nota Fiscal de Serviço | `NFS` | `body.codigo_tipo_documento` | `body.document_type` |

Em títulos, o código segue em `codigo_tipo_documento`; em `account-transactions.create/update`, segue em `detalhes.cTipo`. O wrapper preserva a escolha: não converte `NFS` em `CRT`, Outros ou outro código. Os tipos já utilizados em lançamentos diretos (`ADI`, `BOL`, `CRT`, `CHQ`, `CON`, `CRE`, `DRF`, `DAS`, `DEB`, `DIN`, `DOC`, `GUIA`, `PROT`, `REC`, `RPA`, `TED`, `TRA`) continuam disponíveis. Códigos fora desse conjunto são rejeitados como `invalid_body` antes da leitura de credenciais.

> [!IMPORTANT]
> Disponibilidade no wrapper e aceitação pelo servidor são verificações distintas. O catálogo autenticado confirma os quatro códigos, incluindo `NFS` = Nota Fiscal de Serviço (consulta individual em 08/10/2026). A enumeração pública de `detalhes.cTipo` dos lançamentos diretos não lista `NFS`, `NFE` ou `FAT`; disponibilizá-los no wrapper não comprova que a Omie os aceite nesse serviço. Não houve teste de escrita real. Se o servidor rejeitar o tipo, informe a limitação e não substitua automaticamente o código, não recrie o lançamento nem crie conta a pagar como alternativa automática.

Para a NFS-e 25 da TELL, preserve o tipo solicitado `NFS`, o número da nota em `body.document_number` (`detalhes.cNumDoc`, string não vazia de até 20 caracteres), a contraparte em `body.customer_id` (`detalhes.nCodCliente`) e a identificação complementar em `body.note` (`detalhes.cObs`). Exemplo parcial de body:

```json
{
  "integration_id": "TELL-NFSE-25",
  "amount": 1280.00,
  "document_type": "NFS",
  "document_number": "25",
  "note": "NFS-e 25 — TELL — CNPJ 52.438.909/0001-20"
}
```

Esse fragmento não é uma requisição completa: também são necessários `account_id`, `date` (`dd/mm/aaaa`), `category_code` e o `customer_id` conferido. Informe `project_id` e `departments` conforme a classificação aprovada. Para alteração, forneça `id` ou `integration_id` do lançamento existente e preserve os demais dados. Não invente identificadores, data ou classificação para completar o exemplo.

Esse fluxo chama somente `IncluirLancCC` ou `AlterarLancCC`, não cria conta a pagar, não realiza baixa e não envia comando de conciliação. As requisições não aceitam `conciliar_documento`/`diversos` nem campos de conciliação. Após uma resposta de sucesso, consulte `account-transactions.get` pelo ID retornado e confira conta, valor, tipo, número, observação, fornecedor e os dados de conciliação retornados em `diversos`. A ausência de comando de conciliação não garante o comportamento de configurações ou automações externas da conta.

Fontes: [Catálogo oficial TiposDocumentoCadastro](https://app.omie.com.br/api/v1/geral/tiposdoc/) e [Contrato oficial ContaCorrenteLancamentos](https://app.omie.com.br/api/v1/financas/contacorrentelancamentos/).
