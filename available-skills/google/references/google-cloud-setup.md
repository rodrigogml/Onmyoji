# Configuração do aplicativo Google

## 1. Criar o projeto

1. Acessar o Google Cloud Console.
2. Criar um projeto chamado `Akuma`.
3. Selecionar o projeto recém-criado.

## 2. Ativar APIs

Ativar:

- Gmail API;
- People API;
- Google Drive API;
- Google Calendar API.

## 3. Configurar consentimento OAuth

1. Abrir Google Auth Platform/Consent screen.
2. Escolher External se a conta não estiver em uma organização Google Workspace administrada.
3. Manter o projeto em Testing para uso pessoal e desenvolvimento.
4. Informar nome do aplicativo, e-mail de suporte e e-mail do desenvolvedor.
5. Declarar os escopos utilizados pelo perfil.
6. Adicionar a conta Google como test user.

Os escopos amplos do Gmail e alguns escopos Google Workspace são sensíveis ou restritos. Uma distribuição pública pode exigir verificação OAuth e requisitos adicionais.

## 4. Criar credencial Desktop

1. Abrir Credentials.
2. Selecionar Create credentials → OAuth client ID.
3. Escolher Desktop app.
4. Nomear a credencial, por exemplo `Akuma Desktop`.
5. Baixar o JSON.
6. Armazenar o arquivo fora do Git, preferencialmente em uma pasta `credentials/` ignorada.

Não criar cliente Web. Não cadastrar webhook, domínio, IP público ou redirect HTTPS.

## 5. Preparar a KeePassVault

Criar uma entrada, por exemplo:

```text
APIs/Google:Akuma
```

Use os campos padrão da entrada para a identidade compartilhada da aplicação: `username` recebe o `client_id`, `password` recebe o `client_secret` e `notes` recebe o JSON versionado com os refresh tokens por perfil. O formato é `{"version":1,"profiles":{"rodrigogml":{"refresh_token":"..."}}}`. O bootstrap atualiza somente o perfil selecionado e preserva os demais.

## 6. Criar o perfil

Copiar `configs/google.toml.model` para `configs/google.toml` e criar um perfil local, como:

```text
[profiles.pessoal]
```

Preencher:

- `profile`;
- `credentials_file` somente para perfis legados;
- `scopes`;
- `download_dir`;
- `vault.script`;
- `vault.config`;
- `vault.token_entry`;
- `vault.client_id_field`;
- `vault.client_secret_field`;
- `vault.profiles_field`;
- `vault.auth_json`.

Não colocar access token, refresh token ou client secret no INI.

## 7. Autorizar

Executar:

```text
python scripts/oauth_bootstrap.py --config configs/google.toml --profile pessoal
```

O bootstrap abrirá o consentimento no navegador e iniciará um callback temporário em `127.0.0.1`. Se o navegador não abrir, copiar a URL exibida para o navegador da mesma máquina.

O fluxo não precisa de IP público, domínio, túnel, servidor permanente ou webhook. O fluxo OOB/copy-paste não deve ser usado.

## 8. Testar

Após a autorização, testar primeiro somente leitura:

```json
{"version":1,"service":"gmail","operation":"profile.get"}
```

```json
{"version":1,"service":"people","operation":"connections.list","query":{"pageSize":10},"paginate":true}
```

```json
{"version":1,"service":"drive","operation":"about.get","query":{"fields":"user"}}
```

```json
{"version":1,"service":"calendar","operation":"calendars.list","paginate":true}
```

Executar operações de escrita uma por vez e confirmar explicitamente ações destrutivas.

## Limitações de Testing

Projetos externos em Testing podem emitir refresh tokens com validade limitada. Para uso contínuo ou distribuição pública, revisar o status de publicação, escopos, verificação OAuth e políticas de dados do Google.
