---
name: mini-apps
description: Publica e administra Mini Apps Python no Gateway local do Onmyoji.
---

# Mini Apps

Use `scripts/mini_apps.py` para criar, publicar, consultar, atualizar,
recarregar, despublicar e excluir Mini Apps. O diretório informado deve estar
no workspace configurado do Shikigami; o agente mantém todos os arquivos da
aplicação e o Gateway mantém somente a publicação.

Envie `create --request` com JSON contendo `root_path`, `entrypoint` e `auth`.
Use `auth.type` igual a `claim` ou `basic`; senhas Basic só existem no comando
de criação e não são retornadas pelo Gateway.

## Contrato da publicação

Cada diretório publicado contém um único entrypoint Python, indicado por
`entrypoint`, que exporta `create_app()`. A factory pode ser síncrona ou
assíncrona e retorna um objeto com `handle(request)`. O método pode ser
síncrono ou assíncrono e retorna `HttpResponse`.

```python
from onmyoji_daemon.miniapps import HttpResponse

class App:
    async def handle(self, request):
        return HttpResponse.json({"path": request.path})

async def create_app():
    return App()
```

O Gateway entrega todas as rotas sob `/a/<id>/` ao mesmo método. Ele não serve
arquivos diretamente: a Mini App decide como devolver HTML, assets, downloads,
JSON e streaming. `HttpRequest` preserva método, path, query, headers,
cookies, corpo, metadados de conexão e a identidade autenticada. Corpos acima
de 1 MiB são disponibilizados por `request.body_file` e `request.open_body()`.

Para WebSocket, implemente opcionalmente `async websocket(socket)`. O socket
oferece `receive()`, `send()` e `close()`; a conexão já chega autenticada.

## Autenticação e callbacks

`claim` cria uma URL de ativação de uso único, seguida de cookie Secure,
HttpOnly e SameSite=Strict. Trate `claim_url` como segredo e não a registre.
`basic` exige HTTPS e recebe `username` e `password` somente no create.

Callbacks externos podem ser liberados explicitamente, sem expor todas as
rotas:

```json
{
  "auth": {
    "type": "claim",
    "public_callbacks": [{"path": "/oauth/callback", "method": "GET"}]
  }
}
```

O retorno da criação contém `callback_secret` uma única vez. Configure-o no
provedor externo como `X-Onmyoji-Callback-Secret`; o Gateway limita callbacks
a 30 por minuto por origem. OAuth, SSO e RBAC não são implementados pelo
Gateway: a exceção de callback permite que a Mini App trate seu próprio fluxo.

## Ciclo de vida

Use `reload` após alterar código Python. `unpublish` e `delete` removem acesso
e sessões, mas nunca arquivos. O Gateway libera a instância carregada somente
quando não há request ou WebSocket em andamento. A publicação persistente
mantém a referência ao diretório após restart; não há cópia de arquivos.

Use `events` para diagnósticos. Eventos nunca retornam segredos de autenticação.

## Exposição externa

Primeiro habilite Mini Apps no menu do daemon e instale a confiança da CA local
se o navegador local precisar acessar `https://localhost`. Configure um único
Cloudflare Tunnel com `tunnel --tunnel-action configure --values '<json>'`.
As referências `keepass_profile` e `token_entry` ficam locais; o token nunca é
gravado no registry. `provision --confirm` cria/atualiza Tunnel, ingress e DNS;
depois o serviço controla o processo `cloudflared` e o mesmo hostname expõe
todas as URLs `/a/<id>/`.

Se o executável não estiver disponível, use
`tunnel --tunnel-action install-cloudflared --confirm`. O download confirmado
vem da distribuição oficial e fica no estado local da instância, fora do
repositório e dos arquivos da Mini App.
