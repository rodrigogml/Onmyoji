# OAuth remoto do MCP no Gateway Mini Apps

O Gateway Mini Apps recebe retornos OAuth das integrações MCP pelo Cloudflare Tunnel já configurado. A rota pública é estável por integração, enquanto cada autorização tem uma sessão temporária. O agente apresenta a URL de autorização ao usuário no chat ou Telegram; o usuário autentica no site do provedor e retorna à página de resultado do gateway.

O primeiro consumidor é o MCP BTG Empresas. O gateway cuida de sessão, callback, expiração e encaminhamento. O módulo MCP cuida de escopos, troca do código, conferência do recurso autorizado, renovação e armazenamento seguro dos tokens.

## Endereços e requisitos

Callback BTG MCP: `https://<hostname-do-tunnel>/oauth/mcp/btg/callback`.

Esse endereço não depende de UUID de publicação, cookies de Mini App ou criação de novos apps. O gateway hospeda a página de resultado como parte da extensão OAuth. Não é necessário publicar uma Mini App separada por consentimento. Os callbacks `public_callbacks` existentes continuam exigindo seu cabeçalho secreto; sua autenticação não foi relaxada.

Requisitos para iniciar:

- Gateway HTTPS e Tunnel provisionado em execução, com hostname estável.
- Perfil MCP BTG configurado.
- Callback completo cadastrado no aplicativo do provedor e no perfil MCP, com correspondência exata.
- Execução do gateway/adapter sob identidade capaz de acessar o provedor de segredos do perfil.
- Rota pública OAuth acessível pelo navegador externo; políticas Cloudflare Access/WAF adicionais não podem impedir esse retorno.

O setup do Tunnel não cadastra aplicativos ou redirect URIs nos provedores. Mudar hostname exige atualizar esses cadastros e perfis. O estado local de prontidão não substitui teste real de DNS, TLS e eventual política de borda.

## Comandos para o agente

```powershell
python -m onmyoji_daemon.cli --onmyoji-root <CODEX_HOME> mini-apps oauth info --profile <perfil>
python -m onmyoji_daemon.cli --onmyoji-root <CODEX_HOME> mini-apps oauth start --profile <perfil> --confirm
python -m onmyoji_daemon.cli --onmyoji-root <CODEX_HOME> mini-apps oauth status --session-id <id>
python -m onmyoji_daemon.cli --onmyoji-root <CODEX_HOME> mini-apps oauth cancel --session-id <id> --confirm
```

O CLI administrativo oferece as mesmas ações com `oauth`, `--profile`, `--session-id` e `--confirm`.

`info` retorna callback esperado, URI configurada e `redirectMatches`. `start` exige autorização explícita, devolve sessionId, authorizationUrl, callbackUrl, expiresIn e status. Apresente authorizationUrl no canal atual sem abrir navegador no servidor. A infraestrutura não envia mensagens externas nem notificações sozinha.

`status` e `cancel` usam o ID local; `profile` é opcional para conferir o contexto. A consulta administrativa usa RPC autenticado do daemon; não há endpoint público que retorne perfil, token ou credenciais.

## Sessão e retorno

Estados: creating, awaiting, processing, completed, denied, cancelled, expired, failed e interrupted. Sessões duram no máximo 10 minutos antes do retorno, conforme o adaptador. Apenas uma sessão ativa por integração MCP/perfil é permitida; perfis distintos podem autorizar simultaneamente.

O callback aceita GET com exatamente um state e um code, ou um error do provedor. access_denied encerra como denied; outros erros encerram como failed sem expor a mensagem externa. Um issuer `iss` recebido precisa coincidir com o esperado; adaptadores que declarem `requireIssuer=true` exigem sua presença antes de trocar um código. Uma resposta de erro com state válido pode encerrar a sessão sem issuer, pois não realiza troca de tokens. O state é aleatório e vincula a resposta ao perfil, à URI, à configuração e à sessão. A reivindicação da sessão é atômica; um código recebido nunca é reexecutado após retorno repetido ou reinício.

A URI/issuer/binding do adaptador são conferidos novamente antes da troca. Se o perfil mudou, a sessão falha sem trocar o código. O adaptador recebe o código somente por stdin de um subprocesso local fixo; URLs, comandos e arquivos arbitrários não podem ser fornecidos pelo agente para escolher o receptor.

Após o processamento, o gateway redireciona para `/oauth/result/<sessionId>`, removendo code/state da URL da página final. A página informa apenas o resultado e orienta voltar ao chat. Usa no-store, no-referrer, CSP sem recursos externos e bloqueio de enquadramento. Access logs HTTP do gateway ficam desligados para não registrar query strings OAuth; os eventos funcionais dos Mini Apps permanecem disponíveis. Políticas de logs na borda Cloudflare devem receber a mesma atenção.

Callbacks inválidos não consomem uma sessão válida. Sessões em processamento interrompidas por mais de 5 minutos ficam interrupted; não retomam a troca automaticamente. Falha após o provedor consumir o código ou gravar o token exige conferir a situação pelo adaptador MCP, sem repetir o callback. Cancelamento bloqueia o retorno primeiro; se a limpeza de estado falhar, status informa cancellation_cleanup_failed.

## Adaptador MCP BTG

Fonte: `onmyoji_daemon.mcp.btg.oauth_adapter`, executada no runtime privado do MCP. O Gateway aceita apenas a integração interna `btg`; perfil e módulo são restritos pelo código.

O gateway executa o módulo com o Python privado do MCP e `--onmyoji-root <CODEX_HOME>`. Um objeto JSON v1 chega pelo stdin. A saída deve ser exatamente `{ "ok": true, "data": {...} }` ou um erro sanitizado. Operações:

| action | Entrada adicional | Saída |
| --- | --- | --- |
| describe | profile | issuer, authorizationEndpoint, redirectUri, storage_root, binding; requireIssuer opcional |
| start | profile, state, sessionId, redirectUri | authorizationUrl e expiresIn entre 30 e 600 segundos |
| complete | profile, code, state, sessionId, redirectUri | authenticated=true somente após validar/persistir a autorização |
| cancel | profile, sessionId | Confirmação de limpeza da sessão correspondente, sem revogar outros tokens |

`binding` é uma impressão da configuração não secreta relevante: aplicativo, ambiente, conta/tenant, redirect e referências ao vault. O módulo inclui todas as mudanças que invalidem a sessão. O endpoint de autorização declarado deve ser HTTPS e corresponder à URL devolvida; os parâmetros state, redirect_uri e response_type=code são conferidos pelo gateway.

O adaptador mantém o vínculo local no cofre, valida a sessão antes da troca, protege concorrência, nunca expõe tokens em stdout/stderr e retorna authenticated apenas quando a autorização está salva. Rotação/revogação e fluxos não compatíveis com Authorization Code GET não estão implementados pelo gateway nesta versão.

## BTG Empresas

Perfis MCP usam `oauth_mode="remote"`. Configure `redirect_uri` com o callback retornado por `btg.get_oauth_callback` e cadastre-o no BTG. A chamada `btg.start_authorization` com `confirm=true` cria a sessão no gateway. `btg.get_authorization_status` acompanha o resultado; `btg.cancel_authorization` encerra a sessão. `btg.get_auth_status` informa somente metadados locais do vault.

O adaptador exige o issuer do BTG no retorno com código, preserva tokens existentes ao cancelar e confere empresa/conta consentidas antes de substituir os tokens. Segredos e verifier continuam no KeePass. O monitor de pagamentos não recebe callbacks nem executa OAuth.

Para Banking, o aplicativo BTG precisa ser confidencial. PKCE adicional no aplicativo confidencial e os scopes escolhidos precisam ser validados na homologação; criar aplicativo público não habilita Banking. [Características BTG](https://developers.empresas.btgpactual.com/docs/starting-doc/Integrando/Aplicativos/Caracter%C3%ADsticas).

## Validação

Os testes usam o adaptador BTG simulado, retorno HTTPS sem cookie/cabeçalho secreto, expiração, recusa, duplicidade, concorrência, issuer divergente, configuração alterada e troca recusada. O BTG é testado com tokens/contas simulados, incluindo proteção de tokens antigos e persistência após conferir a conta.

```powershell
python -m pytest onmyoji-daemon/tests/test_mcp_oauth_broker.py onmyoji-daemon/tests/test_mcp_btg_oauth.py -q
```

O teste local HTTPS simula a chegada pelo Tunnel; não provisiona Cloudflare nem concede autorização bancária real. A validação externa exige Tunnel/aplicativo/credenciais configurados na instância. [Diretrizes OAuth](https://www.rfc-editor.org/rfc/rfc9700.html).
