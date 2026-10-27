# Tabelas de dados MCP

`data_tables` oferece registros estruturados para skills próprias do Shikigami. Não substitui bancos privados de módulos MCP de domínio: cada integração continua dona de sua própria persistência quando ela participa do seu contrato.

## Fonte versionada

Cada schema pertence a uma skill do Shikigami:

```text
shikigami/skills/<skill>/schemas/<schema>/
├── schema.toml
└── migrations/
    ├── 0001-inicial.toml
    └── 0002-descricao-em-kebab-case.toml
```

O Setup descobre somente `shikigami/skills/*/schemas/*/schema.toml`. O descritor contém `schema_format = 1`, `id`, `title` e `description`. O diretório deve ter o mesmo nome do `id`.

Cada migration usa o nome `NNNN-descricao.toml`, começa em `0001`, não possui lacunas e repete `schema_id` e `migration_version`. A versão do nome e do conteúdo deve coincidir. Uma migration já aplicada não pode ser alterada, removida ou renomeada: o Setup registra seu nome e SHA-256 no SQLite e bloqueia divergências.

Na primeira versão, migrations só aceitam `create_table`, `add_column` e `create_index`. Não há remoção, renomeação nem reconstrução automática de tabelas.

## Instalação e armazenamento

O operador instala e atualiza schemas em `MCP > Integrações MCP > Tabelas de dados`. O Setup mostra migrations pendentes e exige a palavra `APLICAR`. O agente MCP não possui ferramenta equivalente.

Os bancos ficam em `configs/mcp/data-tables/<schema>.sqlite3`. A configuração local `configs/mcp/data-tables.toml` define quais schemas instalados estão habilitados. Sem schema habilitado, nenhuma ferramenta `data_tables.*` é publicada.

Com ao menos um schema habilitado, o Onmyōji copia e repara o workflow `onmyoji-data-tables` em `<workspace>/.agents/skills/`. A cópia não é link simbólico nem fonte de verdade: é recriada de `onmyoji-daemon/resources/agent-skills/onmyoji-data-tables/` antes de iniciar o Codex-CLI ou o App Server. Ao desabilitar todos os schemas, a cópia gerenciada é removida.

## Operação pelo agente

O agente pode descobrir schemas, descrever tabelas e operar registros por `data_tables.list_schemas`, `data_tables.describe_schema`, `data_tables.list_records`, `data_tables.get_record`, `data_tables.create_record`, `data_tables.update_record` e `data_tables.delete_record`.

O serviço rejeita SQL livre, nomes de tabela ou coluna não declarados, tipos inválidos, campos extras, filtros fora do contrato e operações não autorizadas pela tabela. A estrutura nunca pode ser alterada por uma chamada MCP.
