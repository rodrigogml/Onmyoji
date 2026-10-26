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
Com um perfil, as ferramentas dispensam argumento de perfil e incluem o
contexto na descrição. Com vários, exigem `profile` e disponibilizam
`btg.list_profiles`.

`btg.get_auth_status` devolve presença das credenciais, validade local do token
vinculado ao aplicativo/ambiente/URI, pendência OAuth e conta identificada. O
retorno contém somente estados e contexto, nunca tokens, client secret,
operador ou estado OAuth bruto. `source: "vault-metadata"` distingue esta leitura
de uma consulta bancária; um token local válido não garante que o banco não o
tenha revogado. Não inicia consentimento nem renova tokens.

## Conectar e consultar

No menu do perfil, configure a URI de retorno pelo Gateway/Tunnel e cadastre
exatamente a mesma URI no aplicativo BTG. O callback MCP é
`/oauth/mcp/btg/callback`; o caminho legado permanece independente.
Depois, use **Conectar ao BTG**, confirme a operação e abra o link retornado
no navegador. Gateway, mini-apps e Tunnel precisam estar disponíveis para
esse fluxo remoto; o MCP continua local por stdio.

O callback valida estado, sessão, emissor e vínculo do perfil, troca o código
por tokens, identifica a conta pelo CNPJ e salva os dados da conta no perfil.
Tokens ficam exclusivamente no `notes` criptografado do KDBX. A gravação não
passa tokens em argumentos de processo nem cria staging em texto claro,
preserva os demais campos e recusa substituir um cofre alterado durante a
operação. Escritas OAuth de integrações diferentes no mesmo KDBX são
serializadas por uma trava de sistema operacional ao lado do cofre
(`.oauth.lock`, sem segredos). O runtime privado do MCP inclui PyKeePass e é preparado pelo setup
ao conectar ou testar a conta. Windows e Linux usam o mesmo contrato.

O agente recebe estas ferramentas, além de `btg.get_auth_status`:

| Ferramenta | Responsabilidade |
| --- | --- |
| `btg.get_oauth_callback` | Calcular URI de retorno pelo Gateway/Tunnel. |
| `btg.start_authorization` | Iniciar consentimento e devolver o link; exige `confirm=true`. |
| `btg.get_authorization_status` | Consultar sessão indicada, ativa ou última do perfil. |
| `btg.cancel_authorization` | Cancelar sessão pendente; não revoga consentimento concluído. Exige `confirm=true`. |
| `btg.get_account` | Consultar e conferir a identidade da conta vinculada. |
| `btg.get_balance` | Consultar saldo. |
| `btg.list_statements` | Consultar extrato com datas e tipo explícitos. |
| `btg.list_dda` | Consultar títulos DDA e paginação. |
| `btg.get_barcode` | Consultar código de barras ou linha digitável. |
| `btg.list_bank_payments` | Consultar pagamentos/iniciações no banco, sem enviar pagamentos. |

As consultas renovam tokens quando necessário, usam apenas contratos fechados
e devolvem `source: "bank"`, instante de observação e valores decimais como
texto. Paginação deve ser tratada explicitamente; aceite de envio não é
comprovação de execução. Nenhum desses métodos movimenta valores.

O workspace configurado para o Codex deve existir fora da raiz do Onmyōji:
ele abriga locks operacionais, não segredos. A conta descoberta não pode ser
silenciosamente substituída por outra conta/CNPJ/ambiente. Para outro vínculo,
crie um novo perfil. Uma tela de setup aberta antes do callback não apaga os
dados da conta ao salvar alterações posteriores.

> [!WARNING]
> Enquanto a skill antiga for necessária, use uma entrada KeePass dedicada
> ao perfil MCP. Compartilhar a mesma entrada OAuth entre callbacks distintos
> troca o vínculo dos tokens e pode interromper o outro fluxo. Nenhum perfil
> ou consentimento legado é importado automaticamente.

## Próximas etapas da migração

| Superfície legada | Tratamento no MCP |
| --- | --- |
| `auth.status` | Implementado: diagnóstico local e consulta da sessão remota separados. |
| `auth.start`, `auth.complete`, `auth.callback-info`, `auth.cancel` | Implementados com callback próprio do Gateway e persistência no cofre MCP; conclusão do OAuth é interna, sem método para receber códigos manualmente. |
| `accounts.get`, `balances.get`, `statements.list`, `dda.list`, `barcodes.get`, `payments.remote-list` | Implementados nas seis consultas autenticadas acima. |
| `payments.prepare`, `payments.submit`, `payments.cancel` | Preservar autorização específica, hash da preparação, valores decimais exatos, idempotência e recuperação de resultado incerto. |
| `payments.list`, `payments.get`, `payments.history`, `payments.refresh`, `payments.receipt` | Portar memória rastreável e comprovantes no workspace externo, sem mover dados bancários para configs. |
| `monitor.status`, `monitor.check` e serviço contínuo | Trocar o consumidor interno para o módulo MCP após equivalência do motor de pagamentos. |
| Backup/restauração da memória | Preservar manutenção explícita e bloqueio de reenvio após restauração. |

> [!IMPORTANT]
> A skill BTG, seu callback e monitor permanecem operacionais durante a
> migração. Pagamentos, memória e monitor continuam usando os perfis legados
> até a equivalência dessas etapas; criar um perfil MCP não os altera.

Testes desta etapa usam transporte simulado e KDBX com credenciais sintéticas.
Não iniciam consentimentos reais, acessam contas reais, enviam ou cancelam
pagamentos.
