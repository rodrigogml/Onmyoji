# Configuração

O arquivo real é `CODEX_HOME/configs/keepass.toml`; ele é local e ignorado pelo Git. Use `py -3 setupSkill.py --action configure`; o arquivo inicial é criado automaticamente quando necessário.

Cada perfil aponta para um vault, delimita operações, caminhos de entradas e diretórios permitidos para anexos. Nenhuma senha é armazenada no TOML. Em Windows, prefira `windows_credential_manager`; em Linux, configure `command` com um provedor local que escreva somente a senha na saída padrão.

Em Linux headless, o configurador também oferece **Credencial criptografada do systemd**. Ela é cifrada com `systemd-creds --user --with-key=host`, vinculada ao usuário e à máquina local, e fica em `CODEX_HOME/configs/credentials/`. É indicada para serviços de usuário sem sessão gráfica; não é portátil para outro host e não deve ser usada quando a migração do segredo depender apenas de arquivos copiados.

Ao criar um perfil, escolha entre usar um KDBX existente ou criar um vault local isolado. A segunda opção cria `CODEX_HOME/configs/vaults/<vault>.kdbx`, solicita e confirma a senha sem eco, envia-a somente pelo stdin para `keepassxc-cli db-create --set-password` e a guarda automaticamente no Windows Credential Manager, no `secret-tool` ou na credencial systemd selecionada. Se a criação, a configuração ou o armazenamento seguro falhar, o arquivo KDBX novo é removido e a configuração anterior é restaurada.

O editor do perfil também pode armazenar a senha sem eco no Windows Credential Manager ou no provedor Linux configurado, e testar a abertura do vault sem exibir entradas ou segredos.
