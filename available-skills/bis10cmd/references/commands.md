# Comandos BISCMD 10.0

Comandos aceitos pela skill: `help`, `facade`, `login`, `connect`, `ping`, `session` e `accountStatement`.

`accountStatement` aceita as operações oficiais `create`, `update`, `createTransfer`, `updateTransfer` e `delete`. Escritas exigem o token literal `confirm`; a skill o preserva, mas não o adiciona automaticamente.

Lançamentos `MANUAL` e `TRANSFER` podem ser manipulados pelo cliente. Lançamentos `BILLS` devem ser alterados por seus fluxos de contas a pagar ou receber.

Exemplo de criação:

```json
{
  "version": 1,
  "commands": [
    {"name": "accountStatement", "args": ["create", "accountId", "1", "categoryId", "10", "date", "2026-08-08", "value", "125.30", "displayLine", "Despesa operacional", "confirm"]}
  ]
}
```
