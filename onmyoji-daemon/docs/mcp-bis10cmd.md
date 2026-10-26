# BIS10CMD no MCP interno

A integração executa o cliente Java BISCMD 10.0 contra a fachada EJB remota do BIS10. O servidor MCP abre a conexão e a sessão automaticamente, resolve separadamente as credenciais JNDI/WildFly e BIS10 no vault compartilhado e injeta os quatro valores somente no ambiente do subprocesso.

Cada perfil define contexto de uso, JAR, diretório de trabalho, host, porta, locale e duas referências KeePass. Um único perfil habilitado é implícito; com vários, `bis10cmd.list_profiles` informa nome e contexto.

## Ferramentas

| Ferramenta | Comportamento |
| --- | --- |
| `bis10cmd.ping` | Valida conexão, autenticação e comunicação com a fachada. |
| `bis10cmd.get_session` | Consulta dados básicos da sessão autenticada. |
| `bis10cmd.list_companies` | Lista empresas permitidas para o usuário. |
| `bis10cmd.select_company` | Valida que uma empresa pode ser selecionada. |
| `bis10cmd.list_accounts` | Lista contas financeiras da empresa. |
| `bis10cmd.list_categories` | Lista categorias financeiras da empresa. |
| `bis10cmd.list_cost_centers` | Lista centros de custo ou o rateio de uma categoria. |
| `bis10cmd.get_account_statement` | Consulta um lançamento pelo ID. |
| `bis10cmd.list_account_statements` | Lista lançamentos com filtros e paginação. |
| `bis10cmd.create_account_statement` | Cria lançamento manual. |
| `bis10cmd.update_account_statement` | Altera lançamento manual. |
| `bis10cmd.create_transfer` | Cria transferência entre contas. |
| `bis10cmd.update_transfer` | Altera transferência. |
| `bis10cmd.delete_account_statement` | Exclui lançamento manual ou transferência. |

As operações dependentes de empresa recebem `company_id`; o serviço seleciona a empresa dentro do mesmo processo antes de executar o comando. Isso é necessário porque a sessão Java existe somente durante cada chamada MCP.

Os comandos técnicos `facade`, `login` e `connect` não são publicados como ferramentas: o serviço sempre os orquestra internamente. A ajuda nativa foi substituída pelos contratos e descrições das ferramentas. `ping`, sessão, seleção de empresa, cadastros e todas as operações de lançamentos permanecem acessíveis.

Parâmetros específicos do cliente permanecem em `args`, como pares nome/valor. As ferramentas de escrita exigem `confirm=true`; o serviço acrescenta o marcador nativo `confirm` ao comando Java somente depois dessa validação. Propriedades Java, variáveis `BISCMD_*`, credenciais e um marcador `confirm` manual são recusados em `args`.

## Consultas financeiras

`get_account_statement` recebe `statement_id`. `list_account_statements` exige em `args` `accountId`, `dateFrom` e `dateTo`; aceita ainda `categoryId`, `operation`, `type`, `value` ou `valueMin`/`valueMax`, `offset` e `limit`.

As páginas estruturadas são validadas antes de serem devolvidas. Valores monetários permanecem como texto decimal, e `hasMore=true` exige continuar com `nextOffset` e os mesmos filtros. Lançamentos `BILLS` são apenas consultivos nessa integração; mutações se limitam a `MANUAL` e `TRANSFER`, conforme as regras do cliente.

> [!IMPORTANT]
> O JAR e a pasta `libs` permanecem dependências operacionais externas. O MCP armazena somente configuração sem segredos em `configs/mcp/bis10cmd.toml`.
