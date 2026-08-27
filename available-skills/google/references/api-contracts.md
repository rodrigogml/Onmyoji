# Contrato da API Google

## Envelope

Toda requisição usa `version: 1`, `service`, `operation`, `params`, `query`, `body`, `paginate` e opcionalmente `confirm`.

Respostas têm `ok`, `version`, `service`, `operation` e `data`. Falhas têm `ok: false` e `error.code`/`error.message`.

## Serviços

O registro em `scripts/google.py` é a allowlist de operações:

- Gmail: messages, threads, labels, drafts, attachments, history, profile, filters, forwarding, send-as e delegates.
- People: connections, pessoas, contatos, buscas, grupos e other contacts.
- Drive: files, permissions, comments, revisions, changes, shared drives, about, export, download e upload.
- Calendar: calendars, events, ACL, colors, free/busy, settings, watch e channel stop.

Não aceitar URL ou método HTTP arbitrário.

## Paginação e sincronização

Use `paginate: true` para seguir `nextPageToken`. Para Gmail history, Drive changes e Calendar events, preserve os tokens de sincronização fornecidos pela API e reutilize-os conforme o endpoint.

## OAuth

O refresh token vem da KeePassVault. Access tokens são renovados somente em memória. O wrapper não imprime `access_token`, `refresh_token`, client secret, cabeçalhos Authorization nem URLs com credenciais.

## Operações destrutivas

Exigem `confirm: true`:

- exclusões de Gmail, Drive e Calendar;
- limpeza de calendários;
- exclusão de permissões;
- esvaziamento da lixeira;
- alterações que enviem convites ou modifiquem participantes;
- revogações de acesso.

Atualizações de contatos devem preservar o `etag` retornado pela People API.

## Arquivos

Uploads e downloads devem usar caminhos locais configurados. Não devolver conteúdo binário grande diretamente ao contexto. Respostas não-JSON podem ser transportadas em Base64 somente quando explicitamente solicitado e dentro dos limites configurados.

Referências oficiais:

- https://developers.google.com/gmail/api
- https://developers.google.com/people
- https://developers.google.com/drive/api
- https://developers.google.com/calendar/api
