# Comandos BISCMD

O wrapper aceita o nome do comando sem o prefixo `-` e uma lista de argumentos na mesma ordem esperada pelo BISCMD.

Comandos identificados no código-fonte:

- `companiesList`;
- `certificatesList`;
- `itemsList`;
- `itemUpdate`;
- `itemPriceUpdate`;
- `docFiscalRepair`;
- `fixNFCe`;
- `docFiscalDetail`;
- `nfceList`;
- `validateDocFiscal`;
- `updateDocFiscalStatus`;
- `nfceDownloadXML`;
- `nfceListagemChaves`;
- `nfceInutilizeNumber`;
- `nfceSendOffline`;
- `exportNFe`;
- `exportFile`;
- `importFile`.

Para parâmetros detalhados, executar o comando com `help`, por exemplo:

```json
{"version":1,"command":"itemsList","args":["help"]}
```

Operações fiscais externas e alterações persistentes devem manter os marcadores exigidos pelo BISCMD, como `confirm` e `dryRun`.

Para regenerar o XML de uma NFC-e em `SEFAZPROBLEM` e prepará-la para `nfceSendOffline`, use:

```json
{"version":1,"command":"fixNFCe","args":["docId","123","confirm"]}
```

O comando exige `confirm`, valida a integridade do QR Code, grava ou atualiza `NFCe_XML_OFFLINE_FIX` e retorna o documento ao status `SEFAZOFFLINE`. Ele não transmite à SEFAZ; execute `nfceSendOffline` em uma segunda etapa.

Saídas estruturadas:

- `BISJSON {...}` vira um registro em `data.records`;
- `BISMETA {...}` vira `data.metadata`;
- demais linhas ficam em `data.messages`.

## `docFiscalDetail`

Consulta, sem mutação, um documento fiscal completo e retorna uma linha `BISJSON`.
Aceita um identificador por vez:

```json
{"version":1,"command":"docFiscalDetail","args":["id-do-documento"]}
```

```json
{"version":1,"command":"docFiscalDetail","args":["key","chave-de-44-digitos"]}
```

```json
{"version":1,"command":"docFiscalDetail","args":["serie","valor","number","valor"]}
```

## `nfceList`

Lista NFC-e de uma única empresa e retorna registros `BISJSON`. `companyId` é
obrigatório; os filtros opcionais são combinados com `AND`.

| Parâmetro | Descrição |
| --- | --- |
| `companyId` | ID obrigatório da empresa. |
| `status` | Status operacional da NFC-e. |
| `validationStatus` | Status da validação fiscal. |
| `validationErrorCode` | Código de erro da validação, por igualdade exata. |
| `start` | Início do período de emissão em ISO-8601. |
| `end` | Fim do período de emissão em ISO-8601. |
| `limit` | Máximo de registros, de 1 a 500. |
| `offset` | Deslocamento para paginação. |

```json
{
  "version": 1,
  "command": "nfceList",
  "args": [
    "companyId", "2",
    "validationStatus", "ERROR",
    "validationErrorCode", "BISModules_001052",
    "start", "2026-08-01T00:00:00",
    "end", "2026-08-31T23:59:59",
    "limit", "100"
  ]
}
```
