# Comandos BISCMD 10.0

Comandos aceitos pela skill: `help`, `facade`, `login`, `connect`, `ping`, `session`, `accountStatement`, `accounts`, `categories`, `costCenters`, `companies` e `company`.

`accountStatement` aceita as consultas `get` e `list`, além das operações oficiais `create`, `update`, `createTransfer`, `updateTransfer` e `delete`. Escritas exigem o token literal `confirm`; a skill o preserva, mas não o adiciona automaticamente.

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

## Consultas de lançamentos

Selecione a empresa na mesma sessão; o ID deve ser obtido das orientações da empresa ou de companies.

~~~json
{
  "version": 1,
  "commands": [
    {"name": "company", "args": ["id", "2"]},
    {"name": "accountStatement", "args": ["list", "accountId", "5", "dateFrom", "2026-09-01", "dateTo", "2026-09-30", "limit", "200"]}
  ]
}
~~~

- get id <id>: consulta um lançamento sem bloqueio. ID inexistente retorna erro.
- list accountId <id> dateFrom <ISO-dia> dateTo <ISO-dia>: consulta dias completos, inclusive a data final.
- Filtros opcionais: categoryId, operation (CREDIT/DEBIT), type (MANUAL/TRANSFER/BILLS), value exato ou valueMin/valueMax absolutos.
- Paginação: offset (0 a 100000), limit (1 a 1000; padrão 200); ordenação por data e ID.
- Os resultados ficam em data.lookups, resource=accountStatements. Valores e valores das categorias são strings decimais; operação define débito/crédito.
- hasMore=true exige nova consulta com os mesmos filtros e nextOffset. Só conclua a conferência quando hasMore=false. Se o período mudar durante a leitura, repita a conferência.
- Categorias MANUAL vêm do lançamento; BILLS, do pagamento. A ponta de débito de uma transferência inclui a contrapartida.
- Consultas não exigem confirm e não alteram BILLS. Escritas mantêm as restrições existentes.
- Para suspeitas de duplicidade, siga os critérios da empresa e compare a mesma conta e operação; consulte também os tipos BILLS e TRANSFER, não apenas MANUAL. Valores iguais no lote informado não bastam para excluir movimentos distintos.
- Após criar um lançamento, use get com o ID retornado para confirmar os campos persistidos.
- As consultas cadastrais accounts, categories, costCenters, companies e company também retornam JSON v1; consulte a ajuda do cliente para seus filtros.
