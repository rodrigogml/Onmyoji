# MCP Mercado Livre e Mercado Pago

Esta integração expõe consultas de conta, compras, envios e faturamento do Mercado Livre e consultas de pagamentos e relatórios do Mercado Pago. Ela não cria cobranças, pagamentos, capturas, cancelamentos, reembolsos, payouts ou conexões Wallet Connect.

## Aplicativos, perfis e módulos

Aplicativos representam registros OAuth criados previamente nos portais Mercado Livre ou Mercado Pago. Cada provedor possui catálogo próprio; um aplicativo pode ser reutilizado por vários perfis. Perfis representam contas e contextos de uso do Onmyōji e habilitam módulos individualmente.

| Módulo | Finalidade |
| --- | --- |
| `mercadolivre.account` | Conta vinculada, endereços e meios de pagamento aceitos |
| `mercadolivre.favorites` | Leitura, inclusão e remoção de favoritos |
| `mercadolivre.purchases` | Compras do usuário autenticado, pacotes e descontos |
| `mercadolivre.shipping` | Envios e rastreamento de compras |
| `mercadolivre.billing` | Períodos e detalhes JSON de faturamento ML/MP |
| `mercadopago.payments` | Pagamentos, orders, refunds, chargebacks e meios de pagamento, somente leitura |
| `mercadopago.reports` | Configuração manual, criação, estado e leitura paginada de relatórios |

O servidor publica a união das ferramentas dos módulos habilitados em pelo menos um perfil. Com um perfil, `profile` é implícito. Com vários, o argumento é obrigatório e seu enum contém todos os perfis habilitados. Um perfil selecionável que não habilite o módulo recebe `profile_module_unavailable`; essa validação também ocorre durante a chamada para proteger clientes com `tools/list` em cache.

> [!IMPORTANT]
> Alterações de módulos entram em vigor na próxima chamada `tools/list`. Reconecte clientes que mantenham a lista em cache.

## Configuração e KeePass

A configuração local fica em `configs/mcp/mercado.toml`, ignorada pelo Git. O modelo versionado é `onmyoji-daemon/configs/mcp/mercado.toml.model`. Use **MCP → Mercado Livre / Mercado Pago** no setup para administrar aplicativos e perfis.

Cada aplicativo usa uma entrada KeePass:

- `username`: Client ID;
- `password`: Client Secret;
- `notes`: não armazena tokens de usuário.

Cada autorização `authorization_code` usa outra entrada, dedicada ao perfil e provedor. Seu `notes` contém JSON gerenciado internamente com tokens, expiração, identidade autorizada, escopos, binding e eventual sessão pendente. Não edite esse JSON manualmente. Para `N` perfis ML+MP, espere duas entradas compartilhadas de aplicativos e até `2 × N` entradas individuais de autorização.

> [!CAUTION]
> Nunca grave tokens no TOML, documentação, logs ou banco do broker. O setup valida a presença das credenciais sem exibi-las.

## OAuth e binding

Mercado Livre usa sempre `authorization_code`. Mercado Pago aceita `authorization_code` e `client_credentials`; neste último modo o token fica apenas em memória e é obtido silenciosamente quando necessário. `start_authorization` e `cancel_authorization` retornam `profile_auth_mode_unavailable` nesse modo.

O Gateway oferece callbacks HTTPS separados:

- `/oauth/mcp/mercadolivre/callback`;
- `/oauth/mcp/mercadopago/callback`.

O consentimento usa PKCE S256. O verifier permanece apenas no estado pendente criptografado no KeePass; o broker guarda hash do state e metadados não secretos. A sessão e o código são consumidos uma vez. Cancelar remove somente a sessão pendente e preserva tokens anteriores.

O binding inclui provedor, aplicativo, identidade não secreta do Client ID, perfil, site, Redirect URI, escopos e modo de autenticação. Alterar qualquer desses valores exige nova autorização; alterar apenas os módulos não invalida tokens. Refresh é serializado entre processos. Se um refresh rotativo for consumido e a gravação falhar, o perfil fica bloqueado com `token_rotation_uncertain` até nova autorização.

## Compras, pagamentos e faturamento

Compras do ML são sempre pesquisadas com o usuário obtido da autorização; a ferramenta não aceita `buyer_id`. Dados antigos dependem da retenção e das permissões do endpoint remoto, pois o Onmyōji não mantém banco histórico.

Há três conceitos distintos:

- compras e pagamentos do comprador aparecem nos pedidos Mercado Livre;
- recebimentos e movimentações da conta aparecem nos pagamentos e relatórios Mercado Pago;
- faturamento ML/MP reúne documentos e detalhes contábeis expostos pelos endpoints de billing.

`account_update` aceita somente nome, endereço, localidade, CEP, telefone e nomes da empresa. Atualizações de conta, favoritos e administração manual de relatórios exigem `confirm=true`. Escritas não são repetidas automaticamente quando o resultado remoto é incerto.

## Relatórios

São suportadas as famílias `released_money`, `account_money`, `activities_collection`, `activities_after_collection` e `activities_withdraw`. O formato é sempre CSV. Não são aceitos agendamentos, e-mail, webhook, FTP/SFTP externo ou credenciais de entrega. Relatórios de atividades configuram exclusivamente `internal_sftp` com objeto de dados vazio; uma conta sem essa permissão recebe o erro do provedor, sem fallback.

`report_rows_list` exige relatório disponível, baixa CSV ou ZIP por endpoint fixo e cria cache transitório em `tmp/mcp/mercado/reports`. O leitor limita o arquivo a 100 MiB por padrão, rejeita path traversal, múltiplos arquivos e expansão ZIP excessiva, interpreta BOM e campos multilinha e retorna até 100 linhas por cursor. Caches expiram após 15 minutos. Não há exportação persistente nem banco de relatórios.

## Respostas e erros

Listagens retornam perfil, provedor, módulo, origem remota, instante UTC, itens e paginação. Decimais do JSON remoto são preservados como texto. Cursores são opacos, autenticados e vinculados à ferramenta, perfil e consulta.

Erros MCP têm `isError=true` e um objeto `error` sanitizado. Os códigos principais são `profile_not_found`, `profile_disabled`, `profile_module_unavailable`, `profile_provider_unavailable`, `application_not_found`, `profile_auth_mode_unavailable`, `oauth_required`, `oauth_revoked`, `token_rotation_uncertain`, `provider_permission_denied`, `provider_rate_limited`, `remote_not_found`, `remote_conflict`, `invalid_request`, `confirmation_required`, `report_not_ready`, `report_too_large`, `invalid_report_file` e `network_error`.

Corpo remoto, Authorization header, URL com query sensível e traceback não são retornados. Quando seguro, o erro pode incluir HTTP status e request ID.

## Diagnóstico

1. Confirme que o aplicativo do provedor existe no perfil e que Client ID/Secret estão presentes no KeePass.
2. Confirme que o callback calculado pelo Gateway é idêntico ao Redirect URI cadastrado no portal e no aplicativo local.
3. Consulte `get_auth_status`; se o binding mudou ou houver `token_rotation_uncertain`, autorize novamente.
4. Para `profile_module_unavailable`, habilite o módulo no perfil ou selecione outro perfil.
5. Para `provider_permission_denied`, revise permissões e disponibilidade do recurso na conta; não há fallback para outro canal.
6. Para relatórios pendentes, consulte `report_status_get` antes de repetir `report_rows_list`.

> [!NOTE]
> Todos os testes usam respostas simuladas. A suíte não acessa contas reais nem executa operações financeiras.

## Referências oficiais

- [Bookmarks/Favoritos do Mercado Livre](https://developers.mercadolivre.com.br/pt_br/favoritos)
- [Pedidos e envios associados](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas)
- [Relatórios de faturamento](https://developers.mercadolivre.com.br/pt_br/relatorios-de-faturamento)
- [Referência de relatórios Mercado Pago](https://www.mercadopago.com.br/developers/en/reference/reports/overview)
- [Relatórios de atividades via API](https://www.mercadopago.com.br/developers/pt/docs/reports/activities-reports/api)
