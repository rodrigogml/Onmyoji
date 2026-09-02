# Configuração

Crie `<CODEX_HOME>/configs/bis10cmd.toml` pelo menu da skill ou a partir de `configs/bis10cmd.toml.model`.

Cada perfil referencia duas entradas KeePass independentes:

- JNDI/WildFly: usuário do `ApplicationRealm` autorizado a acessar o EJB remoto;
- BIS10: usuário funcional usado para abrir a sessão na fachada.

Os dois perfis KeePass também podem ser distintos. O configurador sugere `APIs/BIS10CMD:JNDI:<Perfil>` e `APIs/BIS10CMD:BIS10:<Perfil>`, mas esses caminhos podem ser ajustados.

O wrapper não modifica `C:\opt\BIS10CMD\application.properties`. Ele envia host, porta, locale e credenciais pelo ambiente `BISCMD_*`, que tem precedência sobre o arquivo local do cliente.
