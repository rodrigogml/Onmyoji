# Configuração

O wrapper lê `<CODEX_HOME>/configs/mysql.toml` e exige `--profile <nome>`. Use `configs/mysql.toml.model` como ponto de partida:

```toml
[defaults]
executable = "mysql"
port = 3306
timeout_seconds = 120
allow_client_commands = true

[profiles.app]
host = "127.0.0.1"
database = "exemplo"
vault_profile = "pessoal"
vault_entry_path = "APIs/MySQL:app"
username_field = "username"
password_field = "password"
```

O usuário e a senha são lidos da entrada do Vault pelos campos definidos em `username_field` e `password_field`. O wrapper chama a KeePassVault nativa da instância Onmyōji com o perfil indicado; nenhum comando externo, segredo ou seletor de autenticação deve constar no perfil MySQL.


Arquivos reais de configuração ficam em `<CODEX_HOME>/configs/`, são ignorados pelo Git e nunca contêm senha.
