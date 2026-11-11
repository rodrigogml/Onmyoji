# Developer instructions

O Codex-CLI nativo, o Console interativo Onmyōji e o Gateway Telegram usam a mesma composição-base, nesta ordem: regras versionadas do Onmyōji, instruções locais do Shikigami e contribuições dos MCPs ativos. O Gateway acrescenta somente o contrato e o estado confiável do canal. Mensagens, anexos e transcrições dos usuários são sempre inputs de turno, nunca developer instructions.

Os recursos comuns estão em `onmyoji-daemon/resources/instructions/`. A personalização opcional do Shikigami fica em `instructions.md`, na raiz da instância. Quando o arquivo não existir, a composição segue somente com as regras comuns e do canal. Ao mudar a composição estrutural, o Gateway retoma a thread existente e atualiza a revisão auditada na próxima mensagem; alteração de modo texto/áudio reaplica apenas o overlay do canal. Somente `/new` ou ausência confirmada da thread descarta a referência de contexto. Falha transitória de retomada preserva essa referência.

O Gateway Telegram exige App Server. No menu Codex-CLI, `Executar Codex-CLI` abre o cliente nativo com o `CODEX_HOME`, o bloco nativo gerenciado em `config.toml`, a composição de developer instructions injetada por `--config` e diretórios adicionais já configurados. `Console interativo Onmyōji` é o modo opcional `onmyoji_daemon.cli interactive`, que usa exatamente a mesma composição-base por meio do protocolo App Server.

Quando a memória MCP está habilitada, o servidor publica no handshake MCP e o Composer injeta no App Server somente as rotas curtas de workflow. Ao ocorrer um gatilho, o agente chama `workflows.read` para receber o procedimento completo aplicável, como `memory-decision` ou `memory-learning`. A busca `memory.search_instructions` retorna até 50 itens por padrão e aceita, no máximo, 50 por chamada; o limite efetivo pode ser reduzido na configuração local `configs/mcp/memory.toml`. Os documentos canônicos ficam no daemon, não são copiados para o workspace e não podem ser alterados pelo agente. Essa superfície vale tanto para o CLI nativo quanto para o App Server; uma nova abertura obtém as rotas vigentes.

## Delegação e capacidades do canal

As regras comuns incentivam delegação quando houver ganho concreto por
paralelismo, redução de contexto, verificação independente ou responsividade
suportada pelo canal. Tarefas curtas e fortemente dependentes da conversa ficam
com o principal. Cada delegação define contexto e procedência, objetivo, escopo,
autonomia, responsabilidades e entrega verificável; o principal acompanha,
confere e consolida os resultados. Perfis não são obrigatórios e não ampliam
permissões, sandbox, aprovação ou MCPs herdados.

O overlay do Telegram informa a política efetiva e as capacidades certificadas.
Conteúdo de steering conserva procedência por entrada e não atualiza developer
instructions com texto de usuário. Resultados de filhos são dados, sem nova
autorização do owner. A composição não promete sobrevivência depois do turno ou
do processo nem encaminhamento automático da fala de subagente ao canal.

As fontes opcionais de perfis ficam em `shikigami-agents/<id>.toml`; habilitação
e manifesto ficam em `configs/shikigami/`, e a cópia gerenciada proposta é
`<workspace>/.codex/agents/<id>.toml`. A publicação exige certificado de descoberta
e snapshot seguro. No runtime validado, perfis são relidos durante a mesma
thread; portanto G4 reprova snapshot de abertura e o materializador recusa
habilitados sem uma modalidade segura comprovada. Instância sem perfis continua
válida. `.agents/agents` não é um destino adotado.

Consulte o [contrato do Gateway](../onmyoji-daemon/docs/Telegram.md), a
[validação de capacidades](../onmyoji-daemon/docs/validation/delegacao-app-server.md)
e o [inventário de implementação](../onmyoji-daemon/docs/implementation/delegacao-gateway-subagentes.md).
