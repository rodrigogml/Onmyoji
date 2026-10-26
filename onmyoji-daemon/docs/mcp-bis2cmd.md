# BIS2CMD no MCP interno

A integração executa o cliente Java BISCMD contra as fachadas EJB remotas do BIS2. O servidor MCP resolve usuário e senha no vault compartilhado, injeta as credenciais somente no ambiente do subprocesso Java e converte as linhas `BISJSON` e `BISMETA` em resposta estruturada.

Cada perfil define contexto de uso, JAR, diretório de trabalho, host e porta do WildFly, entrada KeePass e raízes locais permitidas para importação e exportação. Um único perfil habilitado é implícito; com vários, `bis2cmd.list_profiles` informa nome e contexto.

## Ferramentas

As ferramentas preservam os comandos oficiais e recebem `args`, uma lista de strings na ordem aceita pelo BISCMD. O argumento `help` solicita a ajuda nativa do comando.

| Ferramenta MCP | Comando BISCMD | Efeito |
| --- | --- | --- |
| `bis2cmd.companies_list` | `companiesList` | Lista empresas. |
| `bis2cmd.certificates_list` | `certificatesList` | Lista certificados. |
| `bis2cmd.items_list` | `itemsList` | Lista itens e códigos. |
| `bis2cmd.item_update` | `itemUpdate` | Altera campos permitidos de item ou código. |
| `bis2cmd.item_price_update` | `itemPriceUpdate` | Altera preço por empresa e código. |
| `bis2cmd.doc_fiscal_repair` | `docFiscalRepair` | Repara campos permitidos de documento fiscal. |
| `bis2cmd.fix_nfce` | `fixNFCe` | Regenera o XML de NFC-e para reenvio. |
| `bis2cmd.doc_fiscal_detail` | `docFiscalDetail` | Consulta documento fiscal completo. |
| `bis2cmd.nfce_list` | `nfceList` | Lista NFC-e com filtros e paginação. |
| `bis2cmd.validate_doc_fiscal` | `validateDocFiscal` | Revalida documento fiscal. |
| `bis2cmd.nfce_download_xml` | `nfceDownloadXML` | Consulta ou persiste XML autorizado. |
| `bis2cmd.nfce_listagem_chaves` | `nfceListagemChaves` | Lista uma página de chaves na SEFAZ. |
| `bis2cmd.nfce_inutilize_number` | `nfceInutilizeNumber` | Inutiliza faixas de numeração. |
| `bis2cmd.nfce_send_offline` | `nfceSendOffline` | Envia NFC-e em contingência offline. |
| `bis2cmd.export_nfe` | `exportNFe` | Exporta XMLs de NFe. |
| `bis2cmd.export_file` | `exportFile` | Exporta payloads de arquivos DB. |
| `bis2cmd.import_file` | `importFile` | Restaura payloads exportados. |

Operações capazes de alterar estado ou chamar serviços fiscais exigem `confirm=true` no contrato MCP. Quando o próprio BISCMD exige o marcador `confirm`, ele também deve permanecer em `args`.

## Arquivos locais

`import_file` aceita `inputPath` somente sob uma raiz de importação do perfil. `export_file` e `export_nfe` aceitam `outputPath` somente sob uma raiz de exportação. O arquivo opcional indicado por `output` em `nfce_download_xml` também precisa estar sob uma raiz de exportação.

> [!IMPORTANT]
> O subprocesso MCP não depende do sandbox do agente. As raízes configuradas no perfil são a fronteira obrigatória para leitura e escrita local do BISCMD.

O JAR e o diretório de trabalho são dependências operacionais externas; não são copiados para `configs/` nem para o workspace. As configurações ficam em `configs/mcp/bis2cmd.toml`, sem segredos.
