# Decisão orientada por memória

## Obrigação e sequência

PARE antes de responder a uma solicitação substantiva, decidir, planejar, escolher, agir, repetir uma tentativa ou perguntar ao usuário ou a terceiros. Execute este procedimento primeiro. Uma solicitação é substantiva quando exige produzir, alterar, recomendar, diagnosticar, executar ou obter informação. Dispense a pesquisa somente em saudações, agradecimentos e mensagens sem decisão, ação ou dúvida.

Siga a ordem: identifique a decisão → pesquise → compare as condições → aplique as regras válidas → aja ou pergunte → avalie o aprendizado.

Leia este workflow integralmente com `workflows.read({"id":"memory-decision"})` quando ele ainda não estiver disponível no contexto atual. Reutilize o procedimento já lido durante a mesma sessão; releia após perda de contexto ou quando houver indicação de atualização. Reutilizar o procedimento não dispensa pesquisar a memória para uma nova solicitação substantiva.

Use somente `memory.*` para consultar instruções salvas. Não procure o SQLite, não leia `configs/` e não crie arquivos paralelos de memória. Ler workflows e executar consultas de memória são etapas deste procedimento: não abra buscas recursivas antes de cada uma delas.

## 1. Separe as decisões da solicitação

1. Identifique o resultado solicitado pelo usuário.
2. Separe apenas decisões que possam ter regras diferentes. Exemplo: escolher um perfil, formatar uma resposta e definir como entregar código são três decisões; executar os comandos de uma entrega já decidida são passos da mesma decisão.
3. Para a decisão atual, identifique quatro elementos, sem pedir ao usuário que os preencha:
   - sistema ou domínio: onde a ação ocorre;
   - objeto: o que será tratado;
   - ação: o que você pretende fazer;
   - dúvida: qual escolha, preferência ou informação ainda falta.
4. Trabalhe uma decisão por vez. Não una assuntos independentes na mesma consulta. Não pesquise todas as decisões imagináveis: pesquise as que a solicitação realmente exige.

## 2. Monte uma consulta curta

1. Escolha de dois a seis termos distintivos dos elementos anteriores. Use nomes de sistemas, objetos e ações que possam aparecer nas instruções salvas.
2. Dê preferência às palavras utilizadas pelo usuário e aos termos de resultados anteriores sobre o mesmo assunto.
3. Retire saudações, pronomes e termos vagos como `regra`, `memória`, `preferência`, `ajuda` ou `importante`, quando eles não identificarem o assunto.
4. Envie palavras-chave separadas por espaços. Não envie a conversa inteira, segredos nem dados pessoais desnecessários.
5. Não use sintaxe SQL, filtros, `AND`, `OR`, aspas de frase ou caracteres curingas para controlar a consulta. O MCP não oferece esses operadores na entrada.

Exemplo de decisão: como entregar alterações prontas do Onmyōji. Uma primeira consulta é `onmyoji entrega commit`. Uma consulta alternativa é `publicar remoto push`. A alternativa troca o vocabulário; não repete a mesma frase com palavras extras.

### O que a busca realmente faz

- Pesquisa palavras em `applies_when`, `instruction` e `keywords`. Não exige correspondência exclusiva nas palavras-chave.
- Ignora diferenças de caixa e acentos na recuperação. Não gaste consultas trocando apenas maiúsculas ou acentos.
- Considera tokens alfanuméricos com pelo menos dois caracteres, até os primeiros vinte tokens da consulta. Pontuação e sublinhados separam termos.
- Combina os tokens com **OU**: qualquer termo pode trazer um resultado. Acrescentar `todoist` não restringe os demais termos a regras do Todoist.
- Não deduz sinônimos, traduções, singular/plural, flexões ou equivalência de significado. Se precisar, você deve trocar os termos em outra consulta.
- Ordena por relevância textual. A primeira posição não significa maior autoridade nem melhor correspondência de contexto.
- Retorna os registros completos e `count`. A busca não informa total, próxima página nem se todos os candidatos foram encontrados.

## 3. Execute a busca e leia os candidatos

1. Chame `memory.search_instructions({"query":"<consulta curta>"})`. Omita `limit` na primeira chamada para respeitar o padrão configurado na instância.
2. Leia `applies_when`, `instruction` e `keywords` de cada registro retornado. Guarde os IDs já examinados no contexto da tarefa para não contar o mesmo resultado duas vezes.
3. Classifique cada registro:
   - **aplicável**: a condição corresponde ao sistema, à situação e ao escopo atuais;
   - **parcial**: cobre apenas parte da decisão; identifique a parte que continua ausente;
   - **inaplicável**: contém palavras parecidas, mas sua condição é de outro caso;
   - **conflitante**: é aplicável, mas sua conduta diverge de outra regra aplicável ou de uma orientação atual.
4. O resultado da pesquisa já contém o texto integral da regra. Use `memory.get_instruction({"id":"<id retornado>"})` se o texto estiver ausente ou truncado no contexto, ou se precisar confirmar o conteúdo vigente. Não invente IDs.
5. Se uma ou mais regras compatíveis resolverem toda a decisão, vá à etapa 5. Não continue pesquisando apenas para acumular resultados.

## 4. Reformule quando faltar resposta

Execute estas tentativas somente para a parte da decisão ainda não resolvida:

1. **Segunda consulta:** substitua os termos por um sinônimo concreto, ação equivalente, tradução conhecida ou outro objeto distintivo do mesmo assunto. Use de dois a quatro termos. Antes de perguntar ao usuário por algo que a memória possa conter, esta segunda tentativa é obrigatória se a primeira não resolver a lacuna.
2. Leia e classifique os novos resultados pela etapa 3. Uma repetição do mesmo ID não é uma evidência adicional.
3. **Terceira consulta:** se a lacuna ou um conflito continuar sem solução, isole um ou dois termos distintivos que ainda não foram bem explorados. Retire o termo genérico que dominou os resultados. Você pode informar `limit: 10` nesta tentativa para examinar mais candidatos.
4. Pare após no máximo três consultas válidas para a mesma decisão nesta rodada. Não faça variações apenas de pontuação, caixa ou acento. Não use `memory.list_instructions` para varrer todo o banco em uma decisão comum.
5. Registre mentalmente o resultado correto: `não encontrei regra aplicável nas consultas realizadas`. Não conclua que a regra não existe nem que o usuário nunca definiu aquela preferência. `count: 0` e resultados limitados não provam ausência global.

Se receber erro, não classifique a memória como vazia. Corrija uma entrada inválida e repita; para falha transitória de acesso, tente novamente uma única vez. Se continuar indisponível, prossiga somente com as informações e autorizações já disponíveis. Não invente regras nem anuncie uma busca bem-sucedida. Informe a indisponibilidade quando ela impedir a decisão ou uma solicitação explícita de consulta à memória.

## 5. Aplique somente regras compatíveis

1. Exclua da decisão os candidatos cuja condição não corresponda ao caso atual.
2. Respeite as instruções superiores, as regras vigentes do projeto e a solicitação atual do usuário. Uma memória não concede autoridade para contrariá-las.
3. Entre regras aplicáveis e compatíveis, aplique todas as necessárias. Se uma regra específica definir uma exceção clara à regra geral para este caso, aplique a específica somente nesse escopo.
4. Não escolha pelo ranking, pelo ID, pela ordem dos resultados ou por uma suposta data: a memória não possui datas nem estado de uso.
5. Se duas regras ainda forem incompatíveis e a hierarquia ou o escopo não resolverem, use a etapa 6 para esclarecer apenas o conflito que afeta a ação.
6. Se a solicitação atual do usuário divergir de uma regra salva, siga a solicitação atual nesta tarefa. Não altere a memória por uma exceção pontual; avalie uma alteração somente se houver indicação de mudança durável.
7. Não transforme ausência de resultado em autorização, proibição ou preferência. A memória não substitui a autorização própria de uma ação externa.

## 6. Aja ou pergunte pelo que ainda falta

1. Se as regras e os dados disponíveis resolverem a decisão, execute a tarefa. Não peça ao usuário que repita uma preferência já recuperada.
2. Se houver uma lacuna factual, verifique as fontes disponíveis e apropriadas à tarefa. Uma regra de procedimento não fornece saldo atual, credencial, estado de serviço ou conteúdo de um arquivo que você ainda não leu.
3. Se a lacuna persistir, confirme que você executou as reformulações da etapa 4 para esse assunto. Pergunte apenas pelo elemento que continua ausente. Não reabra escolhas já resolvidas pela memória ou pela solicitação atual.
4. Se houver conflito não resolvido, descreva de forma breve as duas condutas e pergunte qual vale para o caso. Não despeje todo o conteúdo da memória.
5. Não consulte terceiros nem envie mensagens sem a autorização exigida para essa ação. A obrigação de pesquisar a memória antes de perguntar não concede tal autorização.

## 7. Reconsulte e avalie o aprendizado

1. Faça nova rodada para uma nova solicitação substantiva, nova decisão independente, mudança de contexto, falha que exija mudar de estratégia ou correção do usuário.
2. Não repita a busca entre passos mecânicos da mesma decisão se contexto e regras continuam válidos.
3. Antes de encerrar, verifique se houve correção de conduta, preferência durável, regra para o futuro, solução reutilizável comprovada ou pedido de revisar/excluir memória.
4. Se houve um desses sinais, leia `workflows.read({"id":"memory-learning"})` e execute sua avaliação. A avaliação pode concluir que nenhuma escrita é necessária. Não chame uma mutação antes desse workflow.

## Verificação final

Antes de responder ou agir, confirme: pesquisei a decisão atual; examinei as condições completas; reformulei as buscas insuficientes antes de perguntar; apliquei somente regras compatíveis; não tratei memória como autorização; não repassei ao usuário uma decisão já resolvida. Antes de encerrar, confirme também que avaliei qualquer aprendizado ocorrido.
