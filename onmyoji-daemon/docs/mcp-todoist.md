# Todoist no MCP interno

O módulo `todoist` consulta e gerencia a conta Todoist pelo servidor MCP local. O token de API é resolvido pelo cofre interno e não é enviado ao agente nos argumentos ou nas respostas.

## Configuração

Use **MCP > Perfis de vault** para preparar o cofre e depois **MCP > Integrações MCP > Todoist** para criar um perfil. Informe nome, perfil de vault e entrada da chave; o roteiro verifica a credencial existente e permite utilizá-la, substituí-la ou escolher outra entrada. A descrição define quando o agente deve escolher esse perfil.

As referências da credencial e a habilitação ficam em `configs/mcp/todoist.toml`. O menu de gestão permite alterar o vault, a entrada, a chave e a descrição, testar a conexão, habilitar, desabilitar e remover o perfil.

Sem perfil habilitado com vault configurado, nenhuma ferramenta Todoist é publicada. Com um perfil habilitado, ele é implícito e seu contexto aparece na descrição das ferramentas. Com vários, `todoist.list_profiles` apresenta nomes e contextos e as demais chamadas exigem `profile`.

## Ferramentas e dados

| Grupo | Operações |
| --- | --- |
| Tarefas | Listar tarefas ativas, filtrar, consultar, criar, alterar, concluir, reabrir e remover. |
| Projetos e seções | Listar, consultar, criar, alterar, arquivar, desarquivar e remover; consultar projetos arquivados e colaboradores. |
| Etiquetas | Administrar etiquetas pessoais e listar, renomear ou remover etiquetas compartilhadas. |
| Comentários e lembretes | Listar, consultar, criar, alterar e remover. |
| Conta | Consultar usuário, atividades, configurações de e-mail e notificações; revogar o token. |
| Arquivos e sincronização | Listar e baixar backups, enviar e remover uploads e executar comandos Sync API. |

`todoist.list_tasks` percorre a paginação da API e retorna as tarefas ativas em `tasks`. As operações de rota recebem identificadores em `params`, filtros em `query` e dados de criação ou alteração em `body`, conforme o schema publicado pela ferramenta.

Por exemplo, `todoist.get_task` recebe `{"params":{"task_id":"<id>"}}`; `todoist.create_task` recebe `{"body":{"content":"Revisar proposta"}}`. Com vários perfis, acrescente `profile`.

## Confirmações e arquivos

Conclusão de tarefa, exclusões, arquivamentos, revogação do token e remoção de upload exigem `confirm=true`. Criações, atualizações, reaberturas e desarquivamentos seguem os argumentos publicados sem esse campo obrigatório. `todoist.sync` recebe uma lista de comandos da Sync API; avalie os efeitos de cada comando antes de enviá-lo.

`todoist.upload_file` lê um arquivo local existente indicado por `file_path`; o módulo não configura raízes próprias de upload por perfil. `todoist.download_backup` devolve o conteúdo em base64, sem gravá-lo automaticamente no workspace. As permissões efetivas da conta continuam sendo determinadas pelo Todoist.
