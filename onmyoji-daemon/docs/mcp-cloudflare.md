# Cloudflare no MCP interno

A integração Cloudflare publica operações de zonas e registros DNS pelo servidor MCP local. O API Token permanece em um perfil de vault e nunca integra a descrição das ferramentas, os argumentos ou as respostas.

## Perfis

Cada perfil define:

- contexto de uso apresentado ao agente;
- estado habilitado ou desabilitado;
- perfil de vault, entrada e campo que contêm o API Token;
- `zone_id` padrão opcional.

Quando existe um único perfil habilitado, as ferramentas o selecionam implicitamente. Com múltiplos perfis, `cloudflare.list_profiles` apresenta somente seus nomes e contextos, e as demais ferramentas exigem `profile`.

## Operações

O módulo permite listar e consultar zonas; listar, consultar, criar, atualizar, substituir e remover registros DNS; exportar e importar registros; e consultar o uso de DNS. Criação, atualização, substituição, remoção e importação exigem `confirm=true`.

O `zone_id` informado na chamada tem precedência sobre o padrão do perfil. Nenhum estado operacional da integração é gravado no workspace: configuração e referências de credencial permanecem em `configs/mcp/`.

> [!IMPORTANT]
> Conceda ao API Token somente as permissões e zonas necessárias. O servidor não aceita API Key global nem credenciais OAuth para esta integração.
