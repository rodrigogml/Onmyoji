---
name: mysql
description: Integração ampla com servidores MySQL usando perfis de configuração, cliente nativo ou driver configurado, credenciais obtidas exclusivamente pela KeePassVault e execução de consultas, comandos, scripts, importações, exportações, backups e administração conforme o perfil solicitado. Use quando for necessário conectar, consultar, modificar, diagnosticar ou administrar bancos MySQL.
---

# MySQL

Use esta skill para trabalhar com instâncias MySQL descritas por perfis TOML explícitos.

## Princípios

- Carregar o perfil explicitamente antes de executar operações.
- Obter usuário e senha pela KeePassVault; nunca pedir, exibir ou registrar credenciais no contexto.
- Respeitar o executável, host, porta, socket, banco, parâmetros e ambiente definidos no perfil.
- Permitir execução ampla de SQL e comandos do cliente MySQL quando autorizada pelo usuário e pelo perfil.
- Não impor bloqueios artificiais a `INSERT`, `UPDATE`, `DELETE`, DDL, procedures, administração ou comandos avançados.
- Informar claramente quando uma operação puder causar perda de dados, indisponibilidade ou alteração estrutural.
- Não mascarar erros do servidor nem alterar consultas silenciosamente.
- Nunca registrar senha, string de conexão completa ou conteúdo secreto em stdout, stderr ou arquivos de log.

## Configuração

Perfis ficam em `<CODEX_HOME>/configs/mysql.toml`, são ignorados pelo Git e partem de `configs/mysql.toml.model`:

```text
python scripts/mysql.py --config <CODEX_HOME>/configs/mysql.toml --profile pessoal
```

Use tabelas `[profiles.<nome>]` para perfis adicionais. O perfil deve definir executável, servidor, porta ou socket, banco e a referência da entrada KeePassVault. Usuário e senha são lidos dos campos configurados nessa entrada.

Leia `references/configuration.md` antes de criar ou ajustar perfis.

## Execução

Use `scripts/mysql.py` para validar perfis, obter autenticação pela KeePassVault, executar SQL parametrizado, executar arquivos SQL, consultar metadados, importar/exportar dados, fazer backup/restauração e chamar o cliente MySQL configurado.

Leia `references/operations.md` quando a tarefa envolver operações avançadas, arquivos, administração ou recuperação.

Não invente credenciais, caminhos ou nomes de banco. Se faltar dado obrigatório, localize configurações existentes ou solicite somente a informação ausente.

## Segurança operacional

A skill não bloqueia operações por serem destrutivas. Antes de executá-las, apresente o comando ou consulta final quando houver risco significativo e peça confirmação somente se a solicitação do usuário não for explícita. Nunca substitua a decisão do usuário por uma política fixa da skill.

Use transações, `--dry-run`, backups ou limites apenas quando solicitados, configurados no perfil ou tecnicamente necessários para preservar a operação pedida. Não reescreva SQL para torná-lo "seguro" sem informar.

## Protocolo

Entrada JSON:

```json
{"version":1,"operation":"query","sql":"SELECT 1","params":[]}
```

Operações suportadas incluem `query`, `execute`, `script`, `client` e `ping`. A saída é JSON com `version`, `ok`, `operation` e `data` ou `error`. O wrapper não imprime senha nem repassa segredos em argumentos.

## Recursos

- `scripts/mysql.py`: wrapper de execução e protocolo JSON.
- `scripts/test_mysql.py`: testes locais mockados.
- `references/configuration.md`: formato dos perfis.
- `references/operations.md`: operações e comandos suportados.
- `configs/mysql.toml.model`: exemplo sem segredos.
