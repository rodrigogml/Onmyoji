# Integração BTG Empresas

A implementação oferece consultas PJ, pagamentos individuais e memória persistente com monitoramento por consultas. O código usa contratos extraídos do portal oficial em 2026-10-14; os testes usam respostas simuladas. A homologação com conta e aplicativo BTG ainda depende de credenciais e consentimento da instância.

> [!IMPORTANT]
> A definição da skill está em `available-skills/btg-empresas/SKILL-2026-10-14.proposed.md`. Conforme `AGENTS.md`, ela precisa ser aprovada/incorporada antes da descoberta pelo agente. O código e o setup não ativam conta, consentimento ou serviço automaticamente.

## Configuração e consentimento

Use `setupOnmyoji.py` para selecionar BTG Empresas ou execute `available-skills/btg-empresas/setupSkill.py --action configure`. O contrato JSON de perfis também oferece `profile-schema`, `profile-create`, `profile-update`, `profile-list`, `profile-test` e `profile-delete`.

Um perfil informa ambiente, CNPJ, aplicativo/redirect URI, escopos e referências KeePass. A pasta de trabalho é lida da configuração central `configs/onmyoji-system.toml`, em `codex.project_directory`; o setup BTG não solicita nem grava esse campo em novos perfis. Ela deve existir e ficar fora do CODEX_HOME. Se estiver ausente ou inválida, configure **Codex-CLI → Pasta do projeto** no setup principal. Perfis antigos com `workspace` continuam aceitos quando o caminho coincide com o central; divergências interrompem a operação para evitar abandonar o histórico, sem mover dados automaticamente.

O setup não solicita o ID da conta. Número e agência são filtros opcionais para selecionar uma conta quando houver mais de uma. No sandbox, use o CNPJ fictício `30306294000145`; os dados da conta também são fictícios. Não troque empresa ou conta de um perfil para reaproveitar registros antigos.

Ao salvar um perfil novo, o setup oferece iniciar a conexão OAuth remota e apresenta o link para abrir no navegador. Após o consentimento, o callback consulta `GET /{companyId}/banking/accounts`, exige uma única conta BTG da empresa compatível com os filtros e grava `account_id`, `account_number` e `branch_code` no TOML. Os tokens permanecem no KeePass. Sem conta compatível, com múltiplas contas ou se o perfil mudou durante a conexão, o setup não conclui a descoberta. Corrija empresa/filtros e use **Testar perfil** para retomar. Cancelar a conexão preserva o perfil pendente; pagamentos e monitoramento permanecem bloqueados até obter a conta.

Para configuração por JSON/CLI, crie o perfil sem `account_id` e inicie a conexão explicitamente:

```powershell
py -3 available-skills/btg-empresas/setupSkill.py --action profile-connect --profile <perfil> --confirm
```

Essa ação retorna `authorizationUrl` e `sessionId`; o callback salva a conta mesmo que o comando já tenha encerrado. Consulte `auth.status` com `params.session_id` para acompanhar. A ação `status` do setup distingue configuração válida de conta pronta por `ready` e `pending_accounts`. Perfis já completos continuam sujeitos à conferência da conta consentida e não têm seus identificadores substituídos pela descoberta.

Crie uma entrada KeePass dedicada, com `password` contendo o client secret e `notes` vazio ou `{}`. O perfil KeePass precisa de acesso `read_write`, pois o wrapper guarda e renova tokens em `notes`. Não reutilize notes com anotações pessoais. Tokens, códigos OAuth e verifier não entram na memória de pagamentos. A entrada deve ser distinta por aplicativo/ambiente; perfis que a compartilhem precisam usar o mesmo workspace para compartilhar o lock de renovação.

Escopos mínimos: `openid`, `empresas.btgpactual.com/accounts.readonly` e `brn:btg:empresas:banking:payments`. A consulta DDA usa também `brn:btg:empresas:banking:direct-debit`, além de ativação do serviço no banco. O usuário de produção precisa ter os poderes exigidos pelo BTG. [Autorização](https://developers.empresas.btgpactual.com/docs/starting-doc/Integrando/Fluxo%20de%20autoriza%C3%A7%C3%A3o/Authorization%20Code), [conta](https://developers.empresas.btgpactual.com/docs/tecspecs/Banking%20-%20Accounts-spec), [DDA](https://developers.empresas.btgpactual.com/docs/tecspecs/Banking%20-%20DDA-spec).

O wrapper recebe exatamente um objeto JSON no stdin e retorna `{ "ok": true, "data": ... }` ou `{ "ok": false, "error": ... }`. Execute com Python 3.11+:

```powershell
py -3 available-skills/btg-empresas/scripts/btg.py --config <CODEX_HOME>/configs/btg-empresas.toml --profile <perfil>
```

Envie `{"version":1,"operation":"auth.start","confirm":true}` após autorização para consentimento. Abra a URL retornada. O aplicativo/redirect URI deve estar cadastrado no BTG. Capture manualmente `code` e `state` da URL de callback e envie pelo stdin, sem argumentos de linha de comando:

```json
{"version":1,"operation":"auth.complete","code":"CODIGO_DO_CALLBACK","state":"STATE_DO_CALLBACK","confirm":true}
```

Esse procedimento manual continua disponível em perfis antigos sem `oauth_mode` ou com `oauth_mode="manual"`. Novos perfis sugerem `oauth_mode="remote"` e utilizam o [OAuth compartilhado do Gateway Mini Apps](skill-oauth.md). Nesse modo, configure o callback estável retornado por `auth.callback-info`, cadastre-o no BTG e apresente a authorizationUrl de `auth.start` no chat. O navegador retorna pelo Tunnel e o adaptador confere empresa/conta antes de salvar os tokens. `auth.status` com `params.session_id` acompanha a sessão e `auth.cancel` com confirmação a encerra. `auth.complete` manual é recusado no modo remoto.

O estado local vale 10 minutos e o código vence conforme o banco. Depois do consentimento, execute `profile-test` para conferir o acesso sem movimentar dinheiro. O modelo agora usa um exemplo de URL externa; substitua pelo hostname real do Tunnel.

## Preparação e envio

```json
{
  "version": 1,
  "operation": "payments.prepare",
  "confirm": true,
  "body": {
    "type": "PIX_KEY",
    "amount": "12.34",
    "paymentDate": "2026-10-20",
    "description": "Pagamento fornecedor",
    "document": "FAT-123",
    "detail": {
      "key": {"value": "chave@example.com"},
      "creditParty": {"name": "Nome do beneficiário", "taxId": "12345678901"}
    }
  }
}
```

| Modalidade | `detail` |
| --- | --- |
| `PIX_KEY` | `key.value` e `creditParty.name/taxId` |
| `PIX_QR_CODE` | `emv` e `creditParty.name/taxId` |
| `TED` / `PIX_MANUAL` | `creditParty.name/taxId/account`; account contém type (`CC`, `PG`, `PP`), number, branch, bankCode |
| `BANKSLIP` | `digitableLine`, 47 dígitos e primeiro dígito diferente de 8 |
| `UTILITIES` | `digitableLine` (48) ou `barcode` (44), iniciado por 8 |
| `DARF` | taxPayer (`id`, `name`), expireDate, principalAmount, baselinePeriodDate, treasuryRevenueCode; opcionais feeAmount, fineAmount, referenceNumber |

Valores são decimais em texto, com até duas casas, e persistidos em centavos. DARF exige total coerente com os componentes e pagador correspondente ao perfil. O limite local padrão é BRL 1.000.000,00 por operação, configurável em `max_amount`; não representa limite concedido pelo banco. `dueDate`, `document`, `reference` e `internalDescription` são opcionais. Para boleto/guia, beneficiário, valor e vencimento são conferidos pelo BTG na preparação e antes do envio. Campos de lotes, recorrências ou antecipação não são aceitos.

Após revisar e autorizar o resumo retornado, envie:

```json
{"version":1,"operation":"payments.submit","id":"ID_LOCAL","preparationHash":"HASH_EXIBIDO","confirm":true}
```

O resultado diferencia iniciação aceita, rejeitada e desconhecida. O hash inclui dados/contexto e deve corresponder ao resumo autorizado. O registro é imutável quanto aos dados preparados; uma mudança requer nova preparação, sem contornar a recuperação de um envio incerto. A criação envia apenas um item, embora o contrato bancário use um array.

O BTG exige aprovação no aplicativo/internet banking. A consulta não expõe assinaturas individuais; o wrapper registra aprovação como evidenciada por execução quando observa `CONFIRMED`. Esse estado comprova débito, mas admite estorno. `PROCESSED` sozinho não comprova assinatura humana. A memória não inventa aprovador ou data/hora de aprovação. [Guia](https://developers.empresas.btgpactual.com/docs/starting-doc/Produtos%20de%20API/Conta%20PJ/Pagamentos%20e%20Transfer%C3%AAncias/pagamentos), [ficha técnica](https://developers.empresas.btgpactual.com/docs/tecspecs/Banking%20-%20Payments-spec).

## Consultas e rastreabilidade

`payments.get`, `payments.list` e `payments.history` leem a memória. `payments.refresh` consulta o banco e atualiza evidências. Todas usam ID local, exceto listagem. Campos retornados incluem lançamento (`createdAt`), vencimento (`dueDate`), data solicitada, envio, observação de aprovação, data de pagamento fornecida pelo banco, observação da confirmação e última consulta bem-sucedida. Os instantes bancários de assinatura não são expostos pelo contrato consultado e não são fabricados.

Filtros de `payments.list`: id, submissionState, approvalState, bankStatus, createdFrom/createdTo, dueFrom/dueTo, paymentFrom/paymentTo, limit e offset. Datas de lançamento `YYYY-MM-DD` representam dias completos em São Paulo (UTC-3); instantes ISO precisam de fuso. Datas de vencimento/pagamento são civis. `hasMore` e `nextOffset` controlam a paginação local. `attentionReasons` destaca resultado incerto, aprovação não verificável, erro ou data solicitada passada.

Consultas remotas: `accounts.get` verifica a conta do perfil; `balances.get`; `statements.list` com startDate, endDate e type (`simple`, `withdraw-applications`, `yields`); `dda.list`; `barcodes.get` com codes; `payments.remote-list` com pageNumber/pageSize e filtros do catálogo. A resposta preserva `_links`; o chamador não deve concluir ausência em uma página parcial. Links remotos nunca são seguidos como URLs arbitrárias. O catálogo fechado está em `scripts/api_contracts.json`.

`payments.cancel` recebe ID/hash/confirm e verifica situação recente. O banco valida a elegibilidade do cancelamento pendente ou agendado. A solicitação fica rastreada até confirmação. `payments.receipt` exige pagamento confirmado com ID bancário e salva PDF em `<workspace>/.onmyoji/btg-empresas/receipts/`.

## Monitor contínuo

Configure `monitor_enabled=true` no perfil. Com o daemon instalado, habilite/inicie explicitamente:

```powershell
python -m onmyoji_daemon.cli --onmyoji-root <CODEX_HOME> enable btg-payments-monitor
python -m onmyoji_daemon.cli --onmyoji-root <CODEX_HOME> start btg-payments-monitor
python -m onmyoji_daemon.cli --onmyoji-root <CODEX_HOME> btg-monitor status
```

O supervisor também oferece status, stop e disable para esse serviço. `monitor.status` no wrapper consulta apenas fila/configuração local; a CLI acima fornece a saúde do processo e dos ciclos. `monitor.check` executa um ciclo manual, sem criar ou reenviar pagamentos.

Parâmetros opcionais do perfil: pending_interval (300 segundos), scheduled_interval (3600), confirmed_interval (86400), confirmation_days (7), max_pending_days (30), max_pages (20), timeout_seconds (30). Após 30 dias, pendências passam a consultas diárias e continuam visíveis; não são consideradas pagas/falhas por tempo. Confirmados são revisitados por 7 dias para detectar estorno; essa janela não garante irreversibilidade. Fora dela, `payments.refresh` permite consulta explícita.

O worker retoma a fila após reinício. Reserva transacional antes do envio evita repetição concorrente; leases coordenam atualizações. Timeout mantém resultado desconhecido. A recuperação percorre páginas e exige referência local, conta, valor e modalidade consistentes, sem associação por nome/valor isolados. Consulta falha preserva o estado anterior e informa desatualização. Não há transação distribuída entre SQLite e banco; falhas de persistência após a chamada exigem recuperação.

## Manutenção da memória

A memória contém dados financeiros: restrinja ACLs do workspace, use armazenamento protegido e uma política de backup. SQLite não criptografa o arquivo. Não há limpeza automática de pagamentos/histórico; a retenção nesta versão é integral. Segredos permanecem no KeePass.

`scripts/memory_maintenance.py --config <arquivo> --profile <perfil> backup --confirm BACKUP` cria snapshot consistente do SQLite e dos comprovantes referenciados. A manutenção abrange todos os contextos no mesmo workspace; use apenas mediante pedido explícito de manutenção. O snapshot fica no workspace e deve ser protegido como o original.

Restauração usa `restore --snapshot <snapshot> --destination <workspace-absoluto-novo> --confirm RESTORE`. Nunca sobrescreve uma memória existente. Antes de apontar a pasta central para o destino restaurado, interrompa o monitor e confira a restauração; essa mudança também afeta as outras integrações da instância. Se houver um campo legado `workspace` no perfil BTG, ajuste-o após a migração para coincidir com o destino central. Preparações/envios restaurados ficam com resultado desconhecido para recuperação bancária: um snapshot antigo pode anteceder uma movimentação já realizada. Não reenvie nem recrie pagamentos para contornar essa pendência. A rotina de manutenção não acessa o banco nem renova tokens.

## Validação e limites

Testes contratuais cobrem valores/datas/modalidades, isolamento, OAuth, envio concorrente, timeout, retomada, consulta incompleta/ambígua, cancelamento, confirmação seguida de estorno, comprovantes e restauração. Não executam pagamentos reais.

```powershell
python -m pytest available-skills/btg-empresas/scripts/test_btg.py onmyoji-daemon/tests/test_btg_monitor.py -q
```

Homologar aplicativo, scopes, poderes, formatos e respostas no sandbox antes do uso produtivo. Não estão incluídos lotes, recorrências, cobranças, webhooks, conciliação ERP ou notificações externas. Mudanças na definição de skill exigem proposta conforme `AGENTS.md`.
