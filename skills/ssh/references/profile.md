# Perfil SSH

O arquivo deve conter uma seção `[ssh]`. `host`, `username`, `auth_mode`, `keepass_wrapper`, `keepass_config` e `keepass_entry` são obrigatórios. `port` assume 22 e `timeout_seconds` assume 30.

`auth_mode = password` lê `keepass_password_field` da entrada KeePass. `auth_mode = key` exporta `keepass_key_attachment` com `attachment.export`; o arquivo é criado em `temp_dir`, usado apenas durante a conexão e apagado depois. Se a chave tiver passphrase, use `keepass_key_passphrase_field`.

`known_hosts` deve apontar para um arquivo OpenSSH conhecido. Hosts ausentes são rejeitados deliberadamente para evitar conexão silenciosa a uma máquina impostora.
