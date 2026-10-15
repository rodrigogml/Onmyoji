---
name: btg-empresas
description: Use para consultar conta PJ BTG Empresas, preparar e enviar pagamentos individuais autorizados e acompanhar sua situação com memória estruturada do Onmyōji. Não inclui conta PF, lotes, recorrências, cobranças, webhooks ou conciliação com ERP.
---

# BTG Empresas

Use exclusivamente `scripts/btg.py --config <CODEX_HOME>/configs/btg-empresas.toml --profile <perfil>` e um objeto JSON v1 pelo stdin. O setup é `setupSkill.py`. `<CODEX_HOME>` representa a raiz da instância; os dados de pagamentos e comprovantes ficam no workspace externo do perfil.

Leia [a documentação operacional](../../docs/integrations/btg-empresas.md) para configurar consentimento OAuth, formatos das modalidades, filtros e serviço de monitoramento. Não obtenha credenciais por outros meios: client secret e tokens são administrados pelo wrapper em uma entrada KeePass dedicada.

## Consultar e preparar

`accounts.get`, `balances.get`, `statements.list`, `dda.list`, `barcodes.get` e `payments.remote-list` consultam o BTG. `payments.list`, `payments.get` e `payments.history` consultam a memória local. Informe sempre a origem e a última consulta bem-sucedida; memória antiga não comprova a situação atual do banco.

`payments.prepare` exige `confirm:true` para registrar a preparação local. Aceita uma modalidade individual e retorna ID local, resumo, datas e `preparationHash`. A preparação não cria iniciação no BTG. Para boletos/guias, o wrapper consulta o código e verifica valor, beneficiário e vencimento.

## Enviar e acompanhar

Após autorização explícita do usuário para o resumo completo, use `payments.submit` com ID local, o `preparationHash` exibido e `confirm:true`. Não substitua dados, perfil ou modalidade após a autorização. Para alterar, prepare um novo registro e obtenha autorização para o novo resumo.

Distinga autorização do usuário para envio, aprovação no banco e execução. Sucesso no envio significa iniciação aceita, não pagamento efetivado. A aprovação exige ação dos responsáveis no aplicativo ou internet banking. O wrapper não aprova pagamentos e não fornece identidade ou horário de cada assinatura.

Use `payments.refresh` para verificar o banco e atualizar o histórico. Quando execução for observada, descreva essa evidência; não invente aprovação explícita, aprovador ou horário. Preservar vencimento, data solicitada, lançamento local e data bancária separadamente.

Timeout, resposta incompleta, interrupção ou ausência em consulta incompleta não autorizam reenvio. Mantenha o ID local e consulte o histórico/atualização. Nunca crie nova preparação para contornar uma iniciação incerta; encaminhe a pendência ao usuário. O monitor somente consulta pagamentos existentes.

`payments.cancel` exige autorização específica, ID local, hash e `confirm:true`; o BTG verifica elegibilidade. A solicitação não comprova cancelamento. `payments.receipt` obtém PDF de um pagamento confirmado e retorna arquivo no workspace; não encaminhe a terceiros sem instrução do usuário.

## Limites e memória

Não utilize URLs arbitrárias, SQL, navegador ou outra integração para contornar falhas do wrapper. Relate códigos funcionais e pendências. Não altere manualmente estados bancários nem eventos do SQLite.

`monitor.status` consulta a fila local; a saúde do serviço é consultada pelo daemon. `monitor.check` executa um ciclo de leituras. A execução contínua depende de serviço ativo e `monitor_enabled=true` no perfil.

Backup/restauração são manutenção explícita do workspace inteiro, não uma ação automática de pagamento. Leia a seção correspondente da documentação antes de usar `scripts/memory_maintenance.py`. A restauração bloqueia reenvio de preparações antigas até recuperação bancária.

## Consentimento remoto

Novos perfis sugerem oauth_mode=remote. Consulte `auth.callback-info` para obter o callback estável do Gateway/Tunnel e confira o cadastro no aplicativo BTG. Após autorização explícita, `auth.start` com confirm:true devolve sessionId e authorizationUrl; apresente esse link no canal atual para o usuário abrir no próprio navegador.

O callback é recebido automaticamente e os tokens ficam no KeePass após conferir empresa/conta. Use `auth.status` com params.session_id para acompanhar. `auth.cancel` com params.session_id e confirm:true encerra a sessão. Não use auth.complete manual no modo remoto. Não repita callbacks consumidos nem exponha códigos, verifier ou tokens. Consulte [OAuth compartilhado](../../docs/integrations/skill-oauth.md) para requisitos do Tunnel, expiração e adaptadores.
