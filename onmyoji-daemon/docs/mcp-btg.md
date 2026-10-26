# Migração BTG Empresas para o MCP

## Etapa implementada

**MCP → Integrações MCP → BTG Empresas** administra perfis com nome livre,
descrição, habilitação individual, ambiente, CNPJ e referência ao perfil de vault
compartilhado. A configuração fica em `configs/mcp/btg-empresas.toml`.

Cada perfil usa uma entrada KeePass dedicada: `username` contém Client ID,
`password` contém Client Secret e `notes` fica reservado ao JSON OAuth. O setup
oferece reutilização ou substituição de cada credencial sem exibir valores e
preserva os metadados OAuth. O diagnóstico consulta o cofre e não exige daemon,
Gateway ou conexão com o banco.

Sem perfil habilitado com vault configurado, a integração não publica tools.
Com um perfil, `btg.get_auth_status` dispensa argumento de perfil e inclui o
contexto na descrição. Com vários, exige `profile` e disponibiliza
`btg.list_profiles`.

`btg.get_auth_status` devolve presença das credenciais, validade local do token
vinculado ao aplicativo/ambiente/URI, pendência OAuth e conta identificada. O
retorno contém somente estados e contexto, nunca tokens, client secret,
operador ou estado OAuth bruto. `source: "vault-metadata"` distingue esta leitura
de uma consulta bancária; um token local válido não garante que o banco não o
tenha revogado. Não inicia consentimento nem renova tokens.

A URI pode ser cadastrada para conferir um consentimento existente; sua
presença não inicia uma sessão. Nenhum perfil ou consentimento legado é
importado automaticamente. Escolha explicitamente o vault e a entrada já
existentes se quiser inspecionar esses metadados pelo MCP.

## Próximas etapas da migração

| Superfície legada | Tratamento no MCP |
| --- | --- |
| `auth.status` | Primeira versão em `btg.get_auth_status`, somente diagnóstico local. |
| `auth.start`, `auth.complete`, `auth.callback-info`, `auth.cancel` | Portar cliente OAuth, callback do Gateway, descoberta de conta e persistência no cofre MCP antes da publicação. |
| `accounts.get`, `balances.get`, `statements.list`, `dda.list`, `barcodes.get`, `payments.remote-list` | Portar contratos fechados e consultas autenticadas. |
| `payments.prepare`, `payments.submit`, `payments.cancel` | Preservar autorização específica, hash da preparação, valores decimais exatos, idempotência e recuperação de resultado incerto. |
| `payments.list`, `payments.get`, `payments.history`, `payments.refresh`, `payments.receipt` | Portar memória rastreável e comprovantes no workspace externo, sem mover dados bancários para configs. |
| `monitor.status`, `monitor.check` e serviço contínuo | Trocar o consumidor interno para o módulo MCP após equivalência do motor de pagamentos. |
| Backup/restauração da memória | Preservar manutenção explícita e bloqueio de reenvio após restauração. |

> [!IMPORTANT]
> A skill BTG, seu callback e monitor permanecem operacionais durante a
> migração. Os pagamentos e consultas bancárias continuam usando os perfis
> legados até a equivalência dessas etapas; criar um perfil MCP não os altera.

Testes desta etapa usam cofre simulado e não iniciam consentimentos, acessam
contas reais, enviam ou cancelam pagamentos.
