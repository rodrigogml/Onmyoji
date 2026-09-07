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

## `nfceListagemChaves`

Consulta uma única página da listagem de chaves NFC-e diretamente na SEFAZ. Não use `limit` nem `offset`: a paginação é temporal e usa o cursor devolvido pela própria SEFAZ.

Parâmetros:

- `companyId <id>`: ID da empresa emissora;
- `certificateId <id>`: ID do certificado digital;
- `start <ISO-8601>`: início inclusivo da consulta;
- `end <ISO-8601>`: fim opcional da consulta.

As chaves da página são devolvidas em `data.messages`. `data.metadata` contém `sefaz_status`, `sefaz_message`, `last_emission`, `next_start`, `returned`, `complete` e `truncated`. Quando a SEFAZ responder `101`, a lista ainda está incompleta: conserve o mesmo `end`, repita a consulta usando `start = next_start` e deduplique as chaves entre páginas. Quando responder `100`, `complete` será `true` e não haverá `next_start`.

Se a SEFAZ responder `101` sem `dhEmisUltNfce`, o BISCMD falha explicitamente, pois não há cursor seguro para continuar. O agente também deve interromper e reportar se `next_start` não avançar em relação ao `start` da chamada anterior.

```json
{
  "version": 1,
  "command": "nfceListagemChaves",
  "args": [
    "companyId", "2",
    "certificateId", "6",
    "start", "2026-08-24T09:35:00",
    "end", "2027-08-24T09:35:00"
  ]
}
```

## `nfceInutilizeNumber`

Solicita a inutilização de um ou mais conjuntos de série e faixa de numeração na SEFAZ. É uma operação fiscal externa e exige `confirm`. Informe cada conjunto como `serie`, `numberStart` e `numberEnd`; sem `--workers`, os conjuntos são enviados sequencialmente.

Para enviar diversos conjuntos, inclusive de séries diferentes, em paralelo, use `--workers` com um inteiro de 2 a 5. O valor 1 não é aceito, pois o modo sequencial já é o padrão. O wrapper também valida essa faixa de valores antes de executar o BISCMD.

```json
{
  "version": 1,
  "command": "nfceInutilizeNumber",
  "args": [
    "companyId", "2",
    "certificateId", "6",
    "serie", "1",
    "numberStart", "123", "numberEnd", "125",
    "serie", "2", "numberStart", "130", "numberEnd", "132",
    "--workers", "2",
    "confirm"
  ]
}
```

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
