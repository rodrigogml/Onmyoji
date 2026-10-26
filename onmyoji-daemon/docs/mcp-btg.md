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
(`.oauth.lock`, sem segredos). O runtime privado do MCP inclui PyKeePass e é
preparado pelo setup ao conectar ou testar a conta. Windows e Linux usam o
mesmo contrato.

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
ele abriga locks operacionais, memória financeira e comprovantes, não tokens
ou senhas. Proteja o workspace com ACLs e armazenamento apropriados; o SQLite
não é criptografado. A conta descoberta não pode ser
silenciosamente substituída por outra conta/CNPJ/ambiente. Para outro vínculo,
crie um novo perfil. Uma tela de setup aberta antes do callback não apaga os
dados da conta ao salvar alterações posteriores.

> [!WARNING]
> Enquanto a skill antiga for necessária, use uma entrada KeePass dedicada
> ao perfil MCP. Compartilhar a mesma entrada OAuth entre callbacks distintos
> troca o vínculo dos tokens e pode interromper o outro fluxo. Nenhum perfil
> ou consentimento legado é importado automaticamente.

## Pagamentos e memória

O MCP publica **21 ferramentas** por perfil único habilitado. Além das onze
de OAuth e consultas, existem:

| Ferramenta | Responsabilidade |
| --- | --- |
| `btg.prepare_payment` | Preparar localmente um pagamento individual, com `body` e `confirm=true`. Não envia ao banco. |
| `btg.submit_payment` | Enviar a preparação exata autorizada: `payment_id`, `preparation_hash` e `confirm=true`. |
| `btg.cancel_payment` | Solicitar cancelamento especificamente autorizado, com os mesmos três argumentos. |
| `btg.list_payments` | Listar memória local com `filters`, `limit` e `offset`. |
| `btg.get_payment` | Consultar um registro local por `payment_id`. |
| `btg.get_payment_history` | Consultar eventos imutáveis e evidências por `payment_id`. |
| `btg.refresh_payment` | Consultar o banco e atualizar o registro, sem reenviar. |
| `btg.get_payment_receipt` | Obter PDF de pagamento confirmado, salvo no workspace. |
| `btg.get_monitor_status` | Consultar habilitação e fila locais do perfil. |
| `btg.check_pending_payments` | Executar um ciclo limitado de consultas da fila. Nunca envia pagamentos. |

`confirm=true` representa autorização explícita para a operação, não uma
confirmação genérica de acesso ao servidor. Depois da preparação, o agente
deve apresentar modalidade, conta, beneficiário, valor, datas e resumo completo
e obter autorização específica para o envio. O retorno `preparationHash` deve
ser enviado sem alteração como `preparation_hash`. Alterações exigem outra
preparação e nova autorização; nunca use isso para contornar envio incerto.

O contrato aceita `PIX_KEY`, `PIX_QR_CODE`, `PIX_MANUAL`, `TED`, `BANKSLIP`,
`UTILITIES` e `DARF`, sempre um item por preparação. Valores e componentes DARF
usam texto decimal exato, com no máximo duas casas. O schema publicado detalha
os campos de cada modalidade, sem aceitar lotes ou campos arbitrários. No
boleto/guia, valor, beneficiário e vencimento são conferidos na preparação e
novamente antes do envio. O limite `max_amount` é local e não representa
limite concedido pelo banco; reduzi-lo bloqueia preparações anteriores que o
excedam.

A reserva transacional persiste a tentativa e sua chave de idempotência antes
do envio. Timeout, interrupção ou resposta incompleta mantêm resultado incerto.
`refresh_payment` exige correspondência única de referência local, conta, valor
e modalidade; ausência em consulta parcial não autoriza reenvio. Falhas de
persistência depois da chamada bancária também exigem recuperação, nunca
repetição de envio. Cancelamento exige situação recente e elegibilidade no
banco; solicitar não comprova que foi cancelado.

Iniciação aceita, aprovação bancária e execução são estados distintos. O MCP
não aprova pagamentos. A API usada não informa aprovador ou horário de cada
assinatura. `CONFIRMED` fornece evidência de execução observada e ainda pode
ser seguido de estorno; `PROCESSED` não comprova assinatura humana.

Os registros ficam em `<workspace>/.onmyoji/btg-empresas/payments.sqlite3` e os
PDFs em `receipts/`. Esse formato e o isolamento por empresa/ambiente/conta são
compatíveis com o legado: perfis que apontem para a mesma conta e workspace
enxergam o mesmo histórico, sem copiar, mover ou apagar registros. O campo
`profile` do registro identifica seu criador, não renomeia o perfil MCP atual.
Tokens, identidade do operador, chaves de idempotência e leases não são
publicados nos registros retornados. O histórico distingue lançamento,
vencimento, data solicitada,
observação do envio, última consulta bem-sucedida e data bancária da execução.

Filtros locais: `id`, `submissionState`, `approvalState`, `bankStatus`,
`createdFrom/createdTo`, `dueFrom/dueTo`, `paymentFrom/paymentTo`, `dueOnly`,
`limit` (1–500) e `offset`. Dias de lançamento representam dias completos em
São Paulo (UTC-3); instantes completos precisam de fuso. `hasMore` e
`nextOffset` controlam a paginação. Leituras da memória retornam
`source: "local-memory"`; não são comprovantes de situação bancária atual.

## Monitor por perfil

**Perfil BTG → Pagamentos e monitor** configura limite local, habilitação do
monitor e intervalos. Também permite consultar a fila ou executar um ciclo
manual sem sair do menu. Não inicia serviços automaticamente. Consultas MCP
manuais e pagamentos não exigem daemon; somente o acompanhamento contínuo
depende do serviço `btg-payments-monitor` habilitado e em execução.

O serviço lê tanto os perfis legados quanto os MCP durante a transição. Para
MCP, exige perfil habilitado, vault configurado e `monitor_enabled=true`; executa
somente o módulo interno de consultas, sem scripts da skill. A saúde no daemon
separa perfis MCP pelo prefixo `mcp:` e não publica IDs nem dados financeiros.
`get_monitor_status` não confirma que esse processo esteja vivo.

Os padrões são: pendentes a cada 300 segundos, agendados a cada 3600,
confirmados a cada 86400 por sete dias, até vinte páginas por recuperação.
Após trinta dias, pendências passam a consultas diárias, sem serem tratadas
como quitadas ou falhas. A janela de confirmados não garante irreversibilidade;
`refresh_payment` permite consultar novamente. Reservas coordenam ciclos
manuais, processos e reinícios. Dois perfis do mesmo contexto podem consultar
o mesmo histórico; não habilite dois monitores sem necessidade.

## Próximas etapas da migração

| Superfície legada | Tratamento no MCP |
| --- | --- |
| `auth.status` | Implementado: diagnóstico local e consulta da sessão remota separados. |
| `auth.start`, `auth.complete`, `auth.callback-info`, `auth.cancel` | Implementados com callback próprio do Gateway e persistência no cofre MCP; conclusão do OAuth é interna, sem método para receber códigos manualmente. |
| `accounts.get`, `balances.get`, `statements.list`, `dda.list`, `barcodes.get`, `payments.remote-list` | Implementados nas seis consultas autenticadas acima. |
| `payments.prepare`, `payments.submit`, `payments.cancel` | Implementados com autorização específica, hash, decimais exatos, reserva transacional e recuperação de resultado incerto. |
| `payments.list`, `payments.get`, `payments.history`, `payments.refresh`, `payments.receipt` | Implementados com memória compatível e comprovantes no workspace externo. |
| `monitor.status`, `monitor.check` e serviço contínuo | Implementados nos métodos MCP e no worker interno do daemon, coexistindo com perfis legados. |
| Backup/restauração da memória | Preservar manutenção explícita e bloqueio de reenvio após restauração. |

> [!IMPORTANT]
> A skill BTG, seu callback e monitor permanecem operacionais durante a
> migração. A manutenção de backup/restauração ainda usa o código legado e
> será migrada antes de remover a skill. Criar um perfil MCP não altera os
> perfis legados nem inicia consentimento, pagamento ou serviço automaticamente.

Testes desta etapa usam transporte simulado e KDBX com credenciais sintéticas.
Não iniciam consentimentos reais, acessam contas reais, enviam ou cancelam
pagamentos.
