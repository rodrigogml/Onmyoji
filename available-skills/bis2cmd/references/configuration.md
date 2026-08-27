# Configuração

Crie `configs/bis2cmd.toml` a partir de `configs/bis2cmd.toml.model` e informe o perfil em cada chamada.

```toml
[defaults]
java_path = "java"
timeout_seconds = 180
encoding = "utf-8"

[profiles.turing]
jar_path = "C:/opt/BISCMD/BISCMD-9.0.jar"
working_dir = "C:/opt/BISCMD"
host = "127.0.0.1"
port = 8080
vault_profile = "pessoal"
vault_entry_path = "Servidores/BIS2/BISCMD"
username_field = "username"
password_field = "password"
```

O wrapper obtém usuário e senha pela skill KeePassVault da própria instância Onmyōji. O arquivo BISCMD, diretório de trabalho e Java devem existir localmente; o perfil nunca contém credenciais.
