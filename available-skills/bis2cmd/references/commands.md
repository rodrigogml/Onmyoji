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
- `validateDocFiscal`;
- `updateDocFiscalStatus`;
- `nfceStatusList`;
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
