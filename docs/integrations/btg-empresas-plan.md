# Planejamento da integração BTG Empresas

Data: 2026-10-14. Status: código implementado e validado com respostas simuladas; definição da skill proposta e homologação bancária pendentes. Consulte [a documentação operacional](btg-empresas.md).

## Objetivo e escopo

Oferecer ao agente operações bancárias individuais para contas PJ do BTG Empresas, com memória persistente e acompanhamento dos pagamentos entre sessões. A memória responde o que foi preparado, enviado, observado no banco e efetivamente confirmado, com evidências e datas distintas.

Incluído nesta fase:

- Perfis isolados por ambiente, empresa e conta, autenticação e teste de acesso por consulta.
- Consulta de conta, saldo, extrato, DDA e código de barras.
- Preparação e iniciação individual de boleto, Pix por chave/dados bancários/copia e cola, TED/TEF e guias/tributos suportados.
- Agendamento individual quando aceito pelo contrato da modalidade.
- Consulta de situação, cancelamento permitido pelo banco e obtenção de comprovante.
- Memória estruturada, histórico, recuperação de envios incertos e monitoramento por consultas periódicas.

Fora desta fase: conta PF; pagamentos em lote; recorrências; webhooks; conciliação com Omie; emissão de cobranças; Pix Automático; folha; crédito; câmbio; cartões; busca periódica de novos títulos no DDA; regras para gerar pagamentos; reenvio automático de operações; envio de mensagens ou comprovantes por canais externos.

> [!IMPORTANT]
> O monitor acompanha operações existentes. Ele não cria, aprova, cancela ou reenvia pagamentos. Consultar DDA por solicitação permanece no escopo; descobrir e pagar títulos automaticamente não.

## Fatos bancários e limites conhecidos

As iniciações exigem aprovação pelos responsáveis no aplicativo ou internet banking. A API contempla Pix, boleto, guias, DARF e TED/TEF. A devolução de Pix recebido existe no catálogo, mas fica como extensão posterior. [Guia de pagamentos](https://developers.empresas.btgpactual.com/docs/starting-doc/Produtos%20de%20API/Conta%20PJ/Pagamentos%20e%20Transfer%C3%AAncias/pagamentos).

Pagamentos usam Authorization Code, `openid` e `brn:btg:empresas:banking:payments`. A idempotência bancária dura 24 horas e é vinculada ao operador. `CONFIRMED` evidencia débito, mas admite estorno posterior. [Ficha técnica](https://developers.empresas.btgpactual.com/docs/tecspecs/Banking%20-%20Payments-spec).

Produção exige consentimento de usuário operador ou superior. [Autorização](https://developers.empresas.btgpactual.com/docs/starting-doc/Integrando/Fluxo%20de%20autoriza%C3%A7%C3%A3o/Authorization%20Code). O DDA precisa ser ativado e não abrange contas de consumo ou impostos. [DDA](https://developers.empresas.btgpactual.com/docs/starting-doc/Produtos%20de%20API/Conta%20PJ/Pagamentos%20e%20Transfer%C3%AAncias/d%C3%A9bito-direto-autorizado-dda).

O sandbox tem respostas controladas; não comprova sozinho todos os comportamentos de produção. Tarifas, limites, escopos dos outros produtos e disponibilidade por conta devem ser confirmados. [Cadastro e ambientes](https://empresas.btgpactual.com/developers).

## Arquitetura proposta

| Componente | Responsabilidade |
| --- | --- |
| `available-skills/btg-empresas/` | Fonte reutilizável, wrapper, modelos, testes e futura definição da skill |
| `setupSkill.py` | Configurar perfis, referências ao vault, consentimento e teste de leitura |
| Wrapper Python | Catálogo fechado de operações, validação, autorização de envio e persistência |
| Cliente BTG | OAuth, contratos por produto, paginação, erros e conversão de valores |
| Repositório SQLite | Registro atual, versões preparadas, tentativas, eventos e monitoramento |
| Serviço `btg-payments-monitor` | Consultar operações rastreadas, com retomada após reinício |

Perfis efetivos ficam em `configs/btg-empresas.toml`, sem segredos. Credenciais e tokens ficam no KeePass/provedor seguro do SO. Dados de atividade ficam em `<workspace>/.onmyoji/btg-empresas/`, inclusive SQLite, comprovantes e diagnósticos sanitizados. O workspace precisa ser externo ao `CODEX_HOME`.

O daemon já possui registro de serviços, mas ainda não possui esse monitor nem um agendador genérico disponível. A implementação deve registrar um serviço específico, com perfil/workspace explícitos. Parâmetros operacionais podem ficar na configuração do daemon; o banco de pagamentos e logs de atividade permanecem no workspace.

A integração `memory-store` existente usa SQLite por namespace. Avaliar seu reaproveitamento na etapa de persistência, exigindo transações que gravem estado e evento juntos, bloqueio de envio concorrente e atualizações condicionais. Se a API atual não garantir essas propriedades, usar repositório SQLite próprio, sem alterar globalmente a memória de outras skills.

Não habilitar o monitor ou obter consentimento bancário durante a criação dos artefatos de código. A ativação será feita pelo setup da instância.

## Memória e contrato temporal

A memória de domínio é administrada pelo wrapper. O agente consulta operações específicas; não recebe uma operação genérica para editar estados bancários ou executar SQL arbitrário.

| Registro | Conteúdo e invariantes |
| --- | --- |
| `payment` | UUID local, ambiente, empresa, conta, perfil, modalidade, descrição, documento, referência externa, beneficiário, valor/moeda, estados e datas |
| `paymentPreparation` | Revisão imutável dos dados preparados, resumo e hash do conteúdo; autorização de envio vinculada à revisão |
| `paymentAttempt` | Operação, início/fim, revisão, operador, chave de idempotência, resultado, IDs bancários e erro sanitizado |
| `paymentEvent` | Sequência, origem, instante observado, instante bancário quando fornecido, mudança e referência da evidência; sem reescrita pelo wrapper |
| `paymentMonitoring` | Última tentativa, última consulta bem-sucedida, próxima verificação, falhas consecutivas, revisão concorrente e lease do worker |
| `paymentReceipt` | Referência relativa ao workspace, hash, formato e data de obtenção |

Identificadores bancários devem ser preservados como texto. Relações entre contrato, lote retornado e pagamento serão confirmadas no contrato real: uma iniciação individual pode retornar identificadores intermediários antes do `paymentId`. Não assumir que a resposta de envio contém todos os IDs finais.

Valores monetários locais usam inteiros em centavos; entradas JSON usam decimal em texto validado, sem ponto flutuante. O adaptador precisa confirmar a unidade esperada em cada endpoint antes de converter. IDs de documentos, chaves e dados de conta não devem ser convertidos para números.

| Campo temporal | Significado |
| --- | --- |
| `createdAt` / `updatedAt` | Lançamento na memória / última alteração local |
| `dueDate` | Vencimento do título, se aplicável e conhecido |
| `requestedPaymentDate` | Data escolhida pelo usuário |
| `bankScheduledDate` | Data de agendamento confirmada pelo banco |
| `submittedAt` | Instante local da tentativa de envio |
| `submissionObservedAt` | Instante em que a aceitação bancária foi constatada |
| `userAuthorizedAt` | Autorização do usuário para enviar a revisão preparada |
| `bankApprovedAt` | Aprovação bancária, apenas se a API informar esse instante |
| `approvalObservedAt` | Instante local em que surgiu evidência de aprovação |
| `bankPaymentDate` | Data de pagamento informada pelo banco, com significado validado para a modalidade |
| `bankConfirmedAt` / `confirmationObservedAt` | Instante bancário informado / instante local da constatação de confirmação |
| `lastSuccessfulCheckAt` / `nextCheckAt` | Atualidade e continuidade do acompanhamento |

Instantes usam UTC em ISO 8601; apresentação usa o fuso configurado, inicialmente `America/Sao_Paulo`. Datas civis usam `YYYY-MM-DD`, sem conversão de fuso. Campos desconhecidos ficam nulos. Pix sem vencimento não recebe vencimento fictício. Datas planejadas nunca substituem a evidência de execução.

Índices devem atender consultas por empresa/conta/ambiente, lançamento, vencimento, data solicitada/efetiva, estado e próxima verificação. A unicidade dos IDs bancários inclui seu contexto. A chave de idempotência inclui operador e ambiente. Uma comparação de beneficiário/valor/data pode sinalizar possível duplicidade, mas não prova identidade entre dois pagamentos legítimos.

## Estados, aprovação e evidência

Manter três dimensões separadas:

- **Envio local:** preparado, em envio, aceito, resultado desconhecido ou rejeitado.
- **Aprovação bancária:** desconhecida, pendente, evidenciada ou recusada, somente conforme evidências previstas pelo contrato.
- **Execução bancária:** estado original e interpretação documentada; estados desconhecidos são preservados e sinalizados.

Não transformar `CREATED`, `VALIDATED` ou sucesso HTTP em aprovação humana. Se não houver informação direta sobre aprovação, o wrapper deve apresentar a evidência disponível e sua limitação, sem inventar o aprovador ou a data. Uma execução confirmada pode evidenciar que a operação passou pelo fluxo necessário, sem comprovar quando ou por quem cada assinatura ocorreu.

O envio encerra com ID local, IDs disponíveis e próxima ação humana. A mensagem pode ser “enviado, aguardando aprovação”, “aceito, aprovação ainda não verificável” ou “resultado do envio desconhecido”, conforme a resposta. “Pago” exige a evidência correspondente à modalidade.

Cancelamento solicitado também é rastreado; só atualizar para cancelado quando houver confirmação. Falha de consulta mantém a última situação conhecida e marca desatualização. Confirmação mantém possibilidade de atualização posterior por estorno.

## Envio consistente e recuperação

1. Persistir a preparação e sua revisão antes de qualquer chamada de criação.
2. Vincular a autorização do usuário ao resumo/revisão. Alterar origem, beneficiário, valor, modalidade ou data invalida a autorização anterior.
3. Em transação local, reservar a revisão para envio e gravar a tentativa com chave estável e operador/contexto. Impedir que dois processos enviem a mesma revisão.
4. Enviar ao BTG e persistir a resposta com os identificadores e evento correspondente.
5. Se houver timeout, resposta incompleta ou interrupção, manter resultado desconhecido e iniciar recuperação por consulta/correlação.

Não existe transação única entre SQLite e banco. A reserva antes da chamada e a recuperação após reinício cobrem essa lacuna, sem prometer execução exatamente uma vez em qualquer circunstância.

Uma repetição de transporte, quando suportada pelo contrato, usa o mesmo conteúdo, chave e operador dentro da janela válida; não representa um novo pagamento. Na dúvida, interromper reenvio e consultar. Após a janela bancária, ou se o operador mudou, não reenviar automaticamente. Ausência de uma correspondência em consulta parcial não comprova que o pagamento não foi criado.

Se a gravação da tentativa falhar, não enviar. Se o banco aceitar e a persistência posterior falhar, recuperar a tentativa já registrada. IDs ambíguos exigem revisão; não associar pagamentos somente por valor ou nome.

## Monitoramento por consultas

O worker executa sem LLM e retoma itens vencidos em `nextCheckAt`. Usa lease com expiração para coordenar processos, limites de concorrência e backoff com jitter para falhas transitórias/rate limit. A disponibilidade do serviço e a atualidade dos dados são consultáveis.

Proposta inicial de intervalos, ajustável após validar limites: pendências/processamento a cada 5 minutos; agendados a cada hora, aumentando a frequência perto da data solicitada; confirmados diariamente por 7 dias para detectar estorno. Essa janela é uma escolha operacional local, não uma garantia bancária de irreversibilidade. Fora dela, consultas explícitas podem atualizar o registro.

Pendências próximas ou posteriores à data solicitada/vencimento ficam destacadas no relatório local. Um prazo de acompanhamento excedido suspende consultas intensivas, preserva a pendência e indica necessidade de ação; nunca converte o item em pago ou falho por decurso de tempo.

Token revogado, acesso negado ou operador incompatível exige atenção no perfil. Pagamentos continuam registrados. Paginação incompleta é informada como incompleta. Recuperação de iniciações sem `paymentId` depende de contrato verificável para localizar os IDs intermediários.

Avisos nesta fase aparecem nas respostas e no relatório de pendências. Não instalar heartbeat do Codex nem integrar Telegram/e-mail. Agendamento individual feito no banco continua executando pelo banco mesmo que o monitor local esteja parado.

## Operações propostas do wrapper

Os nomes abaixo são locais; não constituem endpoints ou operações já implementadas.

| Operações | Contrato esperado |
| --- | --- |
| `accounts.get`, `balances.get`, `statements.list`, `dda.list`, `barcodes.get` | Consultas BTG, com contexto e paginação explícitos |
| `payments.prepare` | Validar e registrar uma revisão local; não criar iniciação bancária |
| `payments.submit` | Enviar uma revisão autorizada, mantendo tentativa persistida |
| `payments.refresh` | Consultar o BTG e atualizar um registro rastreado |
| `payments.list`, `payments.get`, `payments.history` | Consultar a memória, informando última atualização e evidências |
| `payments.cancel` | Solicitar cancelamento permitido, com autorização e rastreamento próprios |
| `payments.receipt` | Obter comprovante e guardar no workspace |
| `monitor.status`, `monitor.check` | Consultar saúde ou executar um ciclo de consultas |

Entrada JSON v1 por stdin, perfil/configuração por argumentos. Respostas distinguem falha da chamada, situação do pagamento e situação da persistência. Consulta local não se apresenta como consulta bancária atual. Não aceitar URL, método HTTP ou SQL fornecido pelo agente.

Exemplos de uso: “liste pagamentos enviados que aguardam aprovação”, “consulte por lançamento ou vencimento”, “atualize a situação deste Pix”, “mostre o histórico e o comprovante”. Cada consulta deve indicar empresa, conta, ambiente, filtros e frescor dos dados.

## Privacidade e continuidade

O SQLite contém dados financeiros e precisa de permissões restritas ao usuário/serviço da instância. Não pressupor que SQLite criptografa o arquivo. Segredos não entram no banco, em payloads de auditoria ou logs. Persistir apenas campos necessários e evidências sanitizadas; mascarar dados nas respostas de diagnóstico.

Backup consistente deve incluir memória e comprovantes, com proteção equivalente e restauração validada. Após restauração, tentativas incompletas passam por recuperação antes de permitir reenvio. Retenção é configurável e não apaga automaticamente pendências ou tentativas incertas. Mudança de perfil não permite redirecionar registros antigos a outra empresa/conta.

## Etapas e critérios de aceite

| Etapa | Entrega e validação |
| --- | --- |
| 1. Contratos | Confirmar esquemas, unidades monetárias, escopos, autorização, datas, IDs intermediários, consulta de aprovação, erros e limites por modalidade |
| 2. Persistência | Modelo/migrações locais, histórico atômico, isolamento, filtros, recuperação, backup e concorrência |
| 3. Setup e leituras | Perfis/vault, OAuth/renovação e consultas paginadas; teste de acesso sem escrita bancária |
| 4. Pagamentos individuais | Preparar/enviar/consultar/cancelar/obter comprovante com contratos verificados |
| 5. Monitor | Worker registrado no daemon, retomada, backoff, frescor e pendências locais |
| 6. Skill e homologação | Proposta de instruções, documentação operacional e testes controlados no sandbox |

Testes relevantes: nulos/inválidos, modalidades válidas, precisão e limites de valor, vencimento ausente, separação de datas, duas empresas, isolamento sandbox/produção, IDs intermediários, autorização invalidada por edição, envio concorrente, timeout após aceitação, reinício nos pontos de interrupção, expiração da idempotência, troca de operador, token revogado, falhas de consulta, aprovação parcial quando exposta, agendamento, cancelamento recusado, processamento, confirmação seguida de estorno, paginação incompleta, corrupção/restauração e caminho de comprovante fora do workspace.

O aceite exige que consultas posteriores encontrem o pagamento e todo seu histórico; que falhas não produzam duplicidade; que nenhuma aprovação ou data efetiva seja inventada; e que o acompanhamento retome após reinício sem efetuar novas movimentações.

> [!NOTE]
> Esquemas dinâmicos completos e evidências de aprovação precisam ser obtidos na etapa 1. Se não for possível comprovar aprovação pela API, esse requisito terá limitação explícita e não será anunciado como concluído. Este documento não define contratos ainda não verificados como fatos.

## Fluxo de documentação da skill

Este arquivo é documentação de planejamento do projeto, não uma definição ativa de skill. A futura criação/alteração de instruções `SKILL.md` e referências de definição seguirá o fluxo de arquivos `.proposed.md` do `AGENTS.md`. Nenhuma instrução existente foi alterada neste planejamento.
