---
name: google
description: Integração OAuth 2.0 com Gmail, Contatos via People API, Google Drive e Google Calendar. Usa cliente Desktop com callback loopback local, perfis INI e refresh tokens armazenados exclusivamente na KeePassVault, sem expor credenciais ao contexto, argumentos ou logs.
---

# Google

Use `scripts/google.py` como wrapper JSON seguro para Gmail, People, Drive e Calendar.

## Execução

Sempre informe `--config` com o perfil selecionado e envie JSON pelo stdin.

- Use `version: 1`.
- Use `service` como `gmail`, `people`, `drive` ou `calendar`.
- Use `operation` pertencente ao registro documentado em `references/api-contracts.md`.
- Use `params` para identificadores do caminho, `query` para parâmetros de consulta e `body` para payloads.
- Use `paginate: true` para consolidar respostas paginadas.
- Use `confirm: true` somente para operações destrutivas autorizadas.
- Nunca coloque tokens, códigos OAuth, client secrets ou respostas sensíveis em prompts, argumentos, arquivos ou logs.

## OAuth

Execute `scripts/oauth_bootstrap.py --config <perfil>` para autorizar a conta Google.

O fluxo usa credencial Desktop, servidor temporário em `127.0.0.1` e porta dinâmica. O navegador pode ser aberto manualmente usando a URL exibida pelo bootstrap, mas deve estar na mesma máquina para retornar ao callback local.

O `client_id` e o `client_secret` são lidos, respectivamente, dos campos `username` e `password` da entrada configurada da KeePassVault. Os refresh tokens ficam no campo `notes`, em JSON versionado e separados por perfil. O wrapper seleciona somente o perfil configurado, renova o access token em memória e não imprime credenciais.

Consulte `references/google-cloud-setup.md` para criar o projeto, ativar as APIs, configurar consentimento, cadastrar test users e baixar a credencial Desktop.

## Perfis

Perfis reais ficam em `configs/`, são ignorados pelo Git e devem seguir `google.ini` ou `google_<perfil>.ini`. Use `configs/google.example.ini` como modelo. O formato das notas é `{"version":1,"profiles":{"rodrigogml":{"refresh_token":"..."}}}`.

## Escopo

A skill cobre Gmail, People/Contatos, Drive e Calendar. Não usa IP público, túnel, webhook, servidor público, OAuth OOB ou cliente OAuth Web.

Consulte `references/api-contracts.md` para operações, paginação, permissões e efeitos destrutivos.
