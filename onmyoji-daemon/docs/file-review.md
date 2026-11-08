# Revisão de arquivos por Mini App

O modelo de revisão permite ao usuário comparar um arquivo original com uma
proposta `.proposed.md`, incorporar alterações por bloco e editar o resultado
antes de autorizar a gravação. O agente prepara a revisão e acompanha seu estado;
a aplicação do resultado é uma ação do usuário no navegador.

## Preparação e acesso

Use `mini_apps.diagnose` para verificar HTTPS local e o workspace configurado.
Para acesso externo, o listener de Mini Apps, o Gateway e o Cloudflare Tunnel
precisam estar disponíveis. Consulte [Gateway Mini Apps](mini-apps.md) e
[Publicação externa](publicacao-externa.md).

O agente informa caminhos absolutos de dois arquivos existentes no mesmo
diretório: o original e sua proposta com sufixo `.proposed.md`. O serviço aceita
uma dupla por revisão; o navegador não recebe uma API para escolher outros
caminhos no computador.

```json
{
  "original_path": "C:/projeto/AGENTS.md",
  "proposed_path": "C:/projeto/AGENTS-2026-11-08.proposed.md",
  "confirm": true
}
```

Esses argumentos pertencem à ferramenta `file_review.create`. A resposta contém
`review_id`, os metadados da publicação e `claim_url`, que deve ser entregue ao
usuário. `local_url` e `public_url` indicam os endereços conhecidos;
`public_available` informa se o caminho público está disponível.

> [!IMPORTANT]
> O acesso usa o claim existente de Mini Apps: segredo na URL, prazo de claim,
> sessão HTTPS e restrição a um cliente. Não existe código separado, nova tela de
> autenticação ou nova política de tentativas neste modelo. Trate `claim_url`
> como uma credencial e não a registre em documentação ou fontes versionadas.

`claim_timeout`, `lifetime` e `idle_timeout` podem ser informados como inteiros
positivos, em segundos. Quando omitidos, valem os padrões existentes do Gateway.
O prazo para entrar com o claim é separado da duração e da inatividade da sessão.

## Revisão e confirmação

A revisão mantém três versões com responsabilidades diferentes:

| Versão | Responsabilidade |
| --- | --- |
| Original preservado | Referência imutável do arquivo ao abrir a revisão. |
| Proposta preservada | Referência imutável do conteúdo sugerido pelo agente. |
| Resultado editável | Rascunho que começa igual ao original e recebe as decisões do usuário. |

Os blocos identificam inclusões, exclusões e substituições. Incorporar um bloco
altera o rascunho; ignorar a sugestão preserva o texto atual do resultado. Uma
edição manual também pertence ao rascunho. Salvar esse rascunho não grava o
arquivo original nem constitui aprovação.

Depois de resolver as sugestões, o usuário confere a comparação entre o original
preservado e o resultado final e aciona **Aplicar resultado**. A confirmação é
vinculada à revisão e ao hash daquele resultado. Qualquer edição posterior exige
uma nova conferência antes de aplicar.

> [!NOTE]
> Uma sugestão já decidida não pode ser reaberta nesta primeira versão. O
> resultado continua editável, permitindo ajustes antes da confirmação final.

Antes de aplicar, o serviço verifica novamente o original e a proposta. Mudanças
externas nos arquivos ou uma revisão desatualizada em outra aba produzem um
conflito; o serviço não deve sobrescrever silenciosamente o conteúdo encontrado.
Os snapshots e o rascunho continuam disponíveis localmente para preservar o
trabalho de revisão.

A aplicação preserva a codificação e as quebras de linha suportadas do original,
mantém um backup local e produz um recibo consultável pelo agente. Ao aplicar ou
cancelar pela interface, a publicação é encerrada depois da resposta. O usuário
vê o resultado da operação e o agente pode continuar consultando seu estado pelo
MCP.

Se a gravação terminar antes de uma interrupção na entrega do recibo, o diário
local permite recuperar o resultado na próxima consulta. Uma repetição da
aplicação do mesmo resultado retorna o recibo existente; a consulta de estado
também encerra uma publicação ainda ativa de uma revisão já concluída.

## Proposta integral e aplicação parcial

Uma aprovação integral incorpora a proposta completa e permite remover o
`.proposed.md`, conforme o fluxo vigente de propostas. Um resultado parcial ou
editado pode ser aplicado sem aprovar o restante da proposta; nesse caso, o
`.proposed.md` permanece.

O descarte do restante precisa ser uma escolha explícita do usuário. Cancelar a
revisão não modifica o original nem descarta a proposta. A ferramenta não muda
as regras de criação ou de aprovação de `README.md`, `AGENTS.md`, `SKILL.md` e
demais definições de skills.

## Integração MCP

| Ferramenta | Uso |
| --- | --- |
| `file_review.create` | Cria e publica a revisão; exige os dois caminhos e `confirm=true`. |
| `file_review.status` | Consulta a revisão pelo `review_id`, sem conteúdos nem segredo de claim. |
| `file_review.cancel` | Cancela e despublica a revisão; exige `review_id` e `confirm=true`. |

Exemplo de acompanhamento:

```json
{"review_id": "identificador retornado pela criação"}
```

O estado distingue uma revisão em edição, aplicação concluída, cancelamento e
conflito. A consulta também informa quando uma revisão em edição perdeu acesso
por expiração ou indisponibilidade da publicação. Nenhuma ferramenta MCP aprova
ou aplica o conteúdo em nome do usuário.

As chamadas administrativas usam o RPC autenticado do Daemon nos métodos
`mini-apps.file-review.create`, `mini-apps.file-review.status` e
`mini-apps.file-review.cancel`. A criação permite até 30 segundos de espera;
consulta e cancelamento usam cinco segundos. Erros MCP retornam códigos
sanitizados sem mensagens internas, credenciais ou conteúdo dos arquivos.

## Armazenamento e limites

| Conteúdo | Localização |
| --- | --- |
| Modelo confiável, adaptador e assets | Pacote `onmyoji_daemon.file_review`, na base versionada. |
| Entrada Python, snapshots, rascunho e backup de cada revisão | `file-reviews/<review_id>/` dentro do workspace configurado. |
| Índice operacional da revisão | `configs/daemon/services/mini-apps/file-reviews/`. |
| Publicação, sessões e eventos | Registro existente de Mini Apps em `configs/daemon/services/mini-apps/`. |
| Staging transitório para um original no repositório | `tmp/` da instância Onmyōji. |
| Staging transitório para um original externo | `file-reviews/.staging/` no workspace. |

O conteúdo revisado permanece no workspace, fora das fontes versionadas. A
publicação referencia uma entrada materializada ali que carrega o modelo
confiável da base; cada revisão recebe somente seus arquivos e seu estado.

O limite é de 1 MiB por arquivo de entrada e por resultado. São suportados UTF-8
com ou sem BOM e UTF-16/UTF-32 com BOM. UTF-8 limpo continua sem BOM. A revisão
se destina a texto; outras codificações, controles binários e quebras de linha
mistas são recusados. LF, CRLF ou CR uniforme do original é preservado na
gravação; a edição interna usa LF. A gravação atômica exige staging no mesmo
volume do arquivo original; um destino incompatível é recusado.

Se a gravação terminar e a remoção aprovada do `.proposed.md` falhar, o recibo
informa `cleanup_warning`. Isso distingue uma pendência de limpeza de uma falha
na aplicação do resultado.

> [!WARNING]
> Mini Apps executa código Python confiável no processo do Gateway. A restrição
> de raiz da publicação não é um sandbox de execução. O modelo de revisão
> restringe suas operações aos arquivos vinculados, valida a sessão e a
> confirmação da requisição e mostra Markdown como texto, sem executar HTML
> vindo dos documentos.

## Validação local

Execute a suíte a partir da raiz com `python -B -m pytest -c pytest.ini`. Os testes
usam o armazenamento temporário configurado em `tmp/` e não dependem de um túnel
externo ativo. Publicação externa real requer a infraestrutura preparada da
instância.

O teste `test_file_review_publication.py` percorre o RPC autenticado, claim,
sessão, rascunho, conferência, aplicação parcial e revogação pelo Gateway
compartilhado, com validação TLS e origem pública distinta do Host interno.
Os testes da interface podem ser executados com
`node --test onmyoji-daemon/tests/test_file_review_ui.cjs`.
