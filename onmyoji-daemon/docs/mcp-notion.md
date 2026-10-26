# Notion no MCP interno

A integração Notion publica a API pública de conteúdo pelo servidor MCP local. O token permanece no vault interno e nunca integra argumentos, descrições ou respostas das ferramentas.

## Perfis

Cada perfil define seu contexto de uso, estado habilitado, referência do token no vault e as raízes locais que podem fornecer arquivos para upload. Uma lista vazia de raízes desabilita uploads locais sem afetar as demais operações.

Quando existe um único perfil habilitado, sua seleção é implícita. Com vários perfis, `notion.list_profiles` apresenta nomes e contextos, e as demais ferramentas exigem `profile`.

## Superfície publicada

O módulo cobre as 48 operações anteriormente disponíveis na skill:

- blocos, páginas e propriedades;
- Markdown aprimorado;
- databases, data sources, schemas, templates e queries;
- views e queries de views;
- comentários, busca, usuários e emojis;
- sessões, envio e conclusão de uploads;
- tarefas assíncronas;
- introspecção e revogação OAuth previstas pela API pública.

As operações que alteram conteúdo, uploads, views ou autorização exigem `confirm=true`. Leituras paginadas aceitam `paginate=true` para agregar respostas que usem `has_more` e `next_cursor`.

## Arquivos e segurança

`notion.file_uploads.send` aceita `body.file_path`, mas o servidor resolve o caminho e permite leitura somente quando o arquivo está dentro de uma das `readable_roots` do perfil. O arquivo não é copiado para `configs/`: o MCP apenas o lê durante o envio.

Respostas são sanitizadas recursivamente para remover tokens, segredos, senhas e URLs que carreguem credenciais. O endpoint é fixado em `https://api.notion.com` e a versão suportada em `2026-03-11`.

> [!IMPORTANT]
> Configure como raiz somente o workspace ou o diretório estritamente necessário aos uploads. O MCP não herda as limitações de sandbox aplicadas ao agente.

Consulte a [referência oficial da API Notion](https://developers.notion.com/reference/intro) para os corpos aceitos por cada operação.
