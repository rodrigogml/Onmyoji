# Perfil SSH

Os perfis ficam em `$CODEX_HOME/configs/ssh.toml`, sempre na seção `[profiles.<nome>]`; o workspace do Shikigami não contém essa configuração. A seção `[defaults]` contém `timeout_seconds` e pode conter `temp_dir`, mas cada perfil configurado pelo setup recebe seu próprio `temp_dir` dentro de `<workspace>/.onmyoji/ssh/temporary-keys/`.

Os campos obrigatórios são `host`, `username`, `auth_mode`, `vault_profile` e `vault_entry_path`; `port` assume `22` e `timeout_seconds` assume `30`. `auth_mode` aceita somente `password` ou `key`.

`auth_mode = password` lê `keepass_password_field` da entrada KeePass. `auth_mode = key` exporta `keepass_key_attachment` com `attachment.export`; o arquivo é criado em `temp_dir`, usado apenas durante a conexão e apagado depois. Se a chave tiver passphrase, use `keepass_key_passphrase_field`.

`known_hosts` deve apontar para um arquivo OpenSSH existente. O setup sugere o arquivo padrão do usuário, coleta a chave pública e pede confirmação da impressão digital antes de gravá-la. Hosts ausentes são rejeitados deliberadamente para evitar conexão silenciosa a uma máquina impostora.
