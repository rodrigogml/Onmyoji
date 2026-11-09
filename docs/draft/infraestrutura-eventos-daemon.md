# Infraestrutura de eventos e agendamentos do daemon

**Status:** rascunho vivo de concepção

**Início:** 2026-11-09

**Próximo tema de discussão:** contrato e envelope comum do evento

> [!IMPORTANT]
> Este documento registra a discussão em andamento, inclusive hipóteses ainda prematuras. Ele não é uma especificação de implementação nem indica que as propostas preliminares foram aprovadas.

## Finalidade

Concentrar a concepção de uma infraestrutura persistente no daemon do Onmyōji para receber eventos, aplicar regras, executar ações opcionais e entregar resultados por diferentes canais. O rascunho deve evoluir incrementalmente: ideias podem ser corrigidas, promovidas a decisões ou descartadas à medida que a discussão avançar.

## Intenções já expressas

As seguintes capacidades orientam a discussão, embora seus contratos técnicos ainda precisem ser definidos:

- O daemon persistente deverá futuramente oferecer um agendador recorrente, capaz de disparar tarefas em data e hora determinadas ou segundo uma recorrência configurada.
- Deverá existir um receptor genérico de eventos. Cada entrada deverá conseguir transportar uma solicitação, um payload ou um prompt, além do canal de resposta e dos parâmetros necessários à execução.
- A execução do evento será síncrona e permanecerá aguardando a resposta do App Server ou do Codex CLI. O chamador poderá esperar por essa resposta ou optar por não a acompanhar nem consumi-la; essa escolha não criará um modo assíncrono no receptor.
- Quando o chamador não acompanhar a resposta, o daemon ainda concluirá o processamento e tentará entregar o resultado ao canal configurado.
- Se essa entrega falhar e não houver um chamador aguardando para receber o erro, o daemon registrará a falha em arquivo de log. Para diagnóstico e resolução futura, o registro poderá conter integralmente a identificação do chamador, os dados de entrada do evento, a saída ou o resultado produzido e o erro ou a falha ocorrida.
- Os arquivos e registros de log dessa infraestrutura serão retidos por 20 dias e excluídos automaticamente quando ultrapassarem esse prazo. Essa política não autoriza excluir e-mails, eventos de origem nem outros conteúdos.
- Receber um evento não implicará necessariamente chamar o Codex. As regras poderão ignorar o evento, arquivá-lo, excluí-lo sob proteção explícita, executar somente um script ou chamar o Codex.
- Quando houver chamada ao Codex, deverá ser possível escolher entre reutilizar a thread associada ao canal de saída e criar uma nova thread isolada.
- Os primeiros canais de saída considerados são Telegram, e-mail e retorno síncrono ao script chamador. A infraestrutura deverá admitir outros adaptadores no futuro.
- Um MCP futuro poderá permitir que o agente crie ou registre scripts e os associe a agendamentos. Esses scripts poderão executar localmente e, opcionalmente, emitir eventos para o daemon e receber o respectivo retorno.

> [!CAUTION]
> Exclusão automática não é o comportamento padrão. Qualquer regra que exclua conteúdo deverá ser deliberada, explícita e protegida; até que esse mecanismo seja definido, exclusão permanece apenas como capacidade pretendida, não como autorização operacional.

## Visão preliminar do fluxo

```text
origem (agendamento, e-mail, script etc.)
    -> evento normalizado
    -> regras e roteamento
    -> ação opcional (script e/ou Codex)
    -> entrega ou retorno
        -> se a entrega falhar e ninguém aguardar o erro: registro em log
```

Esta sequência é uma proposta de decomposição conceitual. O caminho de falha sem chamador aguardando já expressa uma decisão funcional, mas ainda não determina componentes, processos, filas, formatos de armazenamento nem garantias de entrega.

### 1. Origem

Uma origem produz o fato ou a solicitação inicial. Exemplos considerados até agora incluem:

- disparo único ou recorrente do agendador;
- verificação ou recebimento de e-mail;
- chamada feita por um script local;
- futuras integrações capazes de emitir eventos para o daemon.

### 2. Evento normalizado

O receptor genérico transforma ou aceita a entrada em um contrato comum. Em princípio, esse contrato precisa representar:

- a solicitação, o payload ou o prompt;
- o canal pelo qual uma resposta deverá ser entregue;
- parâmetros de execução;
- informações suficientes para que regras e roteamento tomem uma decisão.

A forma exata, os campos obrigatórios, a proveniência, a identidade, a correlação e o tratamento de dados sensíveis ainda estão em aberto. Esse envelope será o próximo tema da discussão.

### 3. Regras e roteamento

As regras avaliam o evento e escolhem seu destino e sua ação. A intenção atual admite pelo menos estes resultados:

| Resultado | Comportamento pretendido |
| --- | --- |
| Ignorar | Encerrar o processamento sem executar script ou Codex. |
| Arquivar | Preservar o item conforme a capacidade e a política da origem. |
| Excluir | Exigir regra explícita e proteção adicional; nunca ser o padrão implícito. |
| Executar script | Rodar uma automação local sem tornar o Codex obrigatório. |
| Chamar o Codex | Iniciar processamento pelo agente com a política de thread escolhida. |
| Combinar ações | Permitir que script e Codex participem do mesmo fluxo, caso o desenho futuro aprove essa composição. |

Ainda não foi definida a linguagem das regras, sua precedência, onde serão armazenadas nem como conflitos serão resolvidos.

### 4. Ação opcional

O executor poderá não fazer nada além da ação decidida pela regra. Quando um script for suficiente, o Codex deverá permanecer fora do fluxo. Quando o Codex for necessário, a execução deverá declarar uma das estratégias de contexto:

- **reutilizar contexto:** usar a thread associada ao canal de saída;
- **isolar contexto:** criar uma nova thread para aquela execução.

Essas opções registram a necessidade funcional, mas ainda não definem ciclo de vida, concorrência, retenção, associação de canais a threads ou comportamento em falhas.

### 5. Entrega ou retorno

O resultado poderá ser entregue por um adaptador de canal ou devolvido diretamente ao originador. Os canais inicialmente imaginados são:

- Telegram;
- e-mail;
- retorno síncrono ao script chamador;
- adaptadores adicionais ainda não identificados.

O receptor manterá uma única semântica de execução: o evento continuará em processamento síncrono enquanto aguarda a resposta do App Server ou do Codex CLI. O chamador poderá permanecer aguardando o resultado ou decidir não acompanhar nem consumir essa resposta.

Quando o chamador não acompanhar a resposta, o daemon ainda concluirá o processamento e tentará entregar o resultado ao canal configurado. Se a entrega falhar e não houver um chamador aguardando para receber o erro, o daemon registrará a falha em arquivo de log. Para permitir diagnóstico e resolução futura de problemas, esse registro poderá conter integralmente:

- a identificação do chamador;
- os dados de entrada do evento;
- a saída ou o resultado produzido;
- o erro ou a falha ocorrida.

O contrato comum entre execução e adaptadores, assim como respostas parciais, timeouts e falhas de entrega, permanece indefinido.

> [!NOTE]
> A opção do chamador de não esperar pela resposta não transforma o receptor em assíncrono nem cria um segundo modo de execução. Ainda precisam ser definidos o ciclo de vida dessa execução, a eventual retenção do resultado, timeout e cancelamento.

> [!IMPORTANT]
> Na premissa atual, a instância e seus dados são acessíveis somente ao próprio dono da informação. Por isso, não há categorias de conteúdo proibidas no log por privacidade e não existe requisito funcional de redaction ou mascaramento. Essa premissa de acesso exclusivo do proprietário fundamenta a permissão de registrar integralmente entrada, saída e falha.

Os arquivos e registros de log dessa infraestrutura terão retenção de 20 dias. Quando ultrapassarem 20 dias, serão excluídos automaticamente.

> [!CAUTION]
> A exclusão automática após 20 dias é uma política restrita ao ciclo de vida desses logs. Ela não autoriza excluir e-mails, eventos de origem nem qualquer outro conteúdo processado ou referenciado pela infraestrutura.

## Exemplo exploratório: triagem de e-mail

Um serviço integrado ao MCP de e-mail poderia verificar uma caixa periodicamente. Para cada mensagem encontrada, aplicaria filtros por remetente ou por outras condições e então decidiria o destino, o canal de resposta e a ação.

Possíveis resultados desse exemplo:

- ignorar uma mensagem fora das regras;
- arquivar uma mensagem que não exige processamento;
- executar um script local;
- chamar o Codex e entregar o resultado por e-mail ou por outro canal escolhido;
- excluir somente quando uma regra específica, explícita e protegida autorizar a operação.

> [!NOTE]
> O exemplo ilustra o fluxo e não fecha a periodicidade, a API de e-mail, os filtros, a política de leitura, o modelo de autorização ou a semântica de arquivamento e exclusão.

## Scripts e MCP futuro

Uma possibilidade para evolução é oferecer um MCP pelo qual o agente possa criar ou registrar scripts e agendá-los. Nesse desenho preliminar:

1. o script é registrado para execução local;
2. o agendador dispara o script segundo a configuração;
3. o script pode concluir seu trabalho sem envolver o Codex;
4. opcionalmente, o script emite um evento para o receptor genérico;
5. quando aplicável, recebe um retorno síncrono ou por um canal configurado.

Ainda devem ser discutidos os limites de criação e alteração de scripts, permissões, isolamento, validação, auditoria, tempo máximo de execução e tratamento de resultados.

## Propostas preliminares, não decididas

- Usar um único envelope normalizado entre todas as origens e o mecanismo de regras.
- Separar conceitualmente origem, normalização, decisão, execução e entrega por adaptadores.
- Tratar script e Codex como ações opcionais, em vez de tornar o Codex o centro obrigatório do pipeline.
- Permitir composição de script e Codex quando houver necessidade comprovada.
- Resolver a estratégia de thread como parte explícita da execução do Codex.

Esses pontos são direção de investigação. Podem mudar quando forem definidos o envelope, as garantias operacionais e as fronteiras de segurança.

## Dúvidas em aberto

### Contrato do evento

- Quais campos formam o envelope mínimo comum?
- Como distinguir solicitação, payload e prompt sem perder a representação original?
- Como representar origem, identidade, canal de resposta, parâmetros de execução e correlação?
- Quais dados são obrigatórios e quais pertencem a extensões específicas de cada origem?
- Como sinalizar conteúdo não confiável e impedir que dados externos sejam tratados como instruções privilegiadas?

### Operação e confiabilidade

- Eventos serão persistidos antes da avaliação? Em quais situações?
- Quais estados e garantias de entrega serão necessários?
- Qual será o ciclo de vida da execução quando o chamador optar por não esperar nem acompanhar a resposta?
- O resultado será retido quando o chamador não o consumir? Se for, por quanto tempo e para quem ficará disponível?
- Como tratar repetição, idempotência, timeout, cancelamento, retomada e falhas parciais?
- Qual será o formato, o caminho, a periodicidade da limpeza, o mecanismo de rotação e o eventual limite de tamanho dos logs de falha?

### Regras e segurança

- Como regras serão declaradas, ordenadas, validadas e testadas?
- Quais autorizações serão exigidas para criar agendamentos, scripts e regras?
- Que proteção adicional tornará uma exclusão automática deliberada e segura?
- Quais limites de recursos e isolamento serão aplicados a scripts locais?

### Threads e canais

- O que caracteriza a thread associada a cada tipo de canal?
- Quando uma associação deixa de ser válida ou precisa ser recriada?
- Como execuções concorrentes no mesmo canal serão coordenadas?

## Próximos passos

1. Discutir e registrar o contrato/envelope comum do evento.
2. A partir do envelope, delimitar responsabilidades entre receptor, regras, executor e adaptadores.
3. Definir o modelo de segurança para scripts, exclusão e conteúdo externo.
4. Especificar persistência, estados e garantias operacionais somente após esclarecer os fluxos necessários.
5. Converter propostas em decisões explícitas antes de iniciar um plano de implementação.

## Registro evolutivo

Esta seção acompanha a evolução do rascunho sem transformá-lo em documentação definitiva. Uma entrada deve indicar se apenas registrou uma ideia, corrigiu entendimento, tomou uma decisão ou descartou uma hipótese.

| Data | Estado | Registro |
| --- | --- | --- |
| 2026-11-09 | Concepção inicial | Registradas as intenções de agendamento, recepção genérica de eventos, regras, execução opcional por script ou Codex, estratégias de thread, canais de saída e integração futura por MCP. Nenhuma arquitetura de implementação foi aprovada. |
| 2026-11-09 | Decisão funcional | A execução do evento será síncrona e aguardará a resposta do App Server ou do Codex CLI. O chamador poderá esperar ou não acompanhar nem consumir a resposta, sem que isso crie um modo assíncrono no receptor. Ciclo de vida, retenção do resultado, timeout e cancelamento permanecem em aberto. |
| 2026-11-09 | Decisão funcional | Sem um chamador acompanhando a resposta, o daemon ainda concluirá o processamento e tentará entregar o resultado ao canal configurado. Se a entrega falhar e ninguém estiver aguardando para receber o erro, registrará a falha em arquivo de log. Para diagnóstico e resolução futura, o log poderá conter integralmente identificação do chamador, entrada do evento, saída ou resultado e erro ou falha. Não há categorias proibidas por privacidade sob a premissa de acesso exclusivo do proprietário. |
| 2026-11-09 | Decisão funcional | Os arquivos e registros de log dessa infraestrutura terão retenção de 20 dias e serão excluídos automaticamente ao ultrapassar esse prazo. A política se aplica somente aos logs e não autoriza excluir e-mails, eventos de origem ou outros conteúdos. |

### Decisões consolidadas

- A execução do evento será síncrona e permanecerá aguardando a resposta do App Server ou do Codex CLI.
- O chamador poderá escolher esperar pela resposta ou não a acompanhar nem consumi-la.
- A escolha de não esperar não criará um modo assíncrono no receptor.
- Sem acompanhamento do chamador, o daemon ainda concluirá o processamento e tentará entregar o resultado ao canal configurado.
- Se essa entrega falhar e não houver chamador aguardando para receber o erro, o daemon registrará a falha em arquivo de log.
- Para diagnóstico e resolução futura, o log poderá conter integralmente a identificação do chamador, os dados de entrada do evento, a saída ou o resultado produzido e o erro ou a falha ocorrida.
- Não haverá categorias de conteúdo proibidas no log por privacidade, sob a premissa de que a instância e os dados são acessíveis somente ao próprio proprietário.
- Os arquivos e registros de log terão retenção de 20 dias e serão excluídos automaticamente quando ultrapassarem esse prazo.
- A política de exclusão automática se restringirá ao ciclo de vida dos logs e não autorizará excluir e-mails, eventos de origem ou outros conteúdos.

Essas são decisões funcionais. O ciclo de vida quando o chamador não espera, a retenção do resultado fora dos logs, timeout, cancelamento e os demais detalhes técnicos ainda não foram decididos. Para o log, permanecem em aberto somente formato, caminho, periodicidade da limpeza, mecanismo de rotação e eventual limite de tamanho. As demais seções continuam organizando intenções, propostas e perguntas para a discussão.
