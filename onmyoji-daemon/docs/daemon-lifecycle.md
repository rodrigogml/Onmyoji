# Controle do daemon

O ciclo de vida do supervisor pertence a `management.daemon_action`. Setup, assistentes e CLI usam esse contrato para iniciar, parar e reiniciar a mesma instância, respeitando seu modo de execução:

| Registro da instância | Controle utilizado |
| --- | --- |
| Serviço registrado em `configs/daemon/service.json` | systemd no escopo registrado ou Serviço do Windows |
| Sem serviço registrado | Processo local em segundo plano |

A existência de um serviço impede a inicialização e a finalização pelo motor de processo local, inclusive a finalização forçada. Uma falha no serviço não cria um processo alternativo. No Linux, o controle de um serviço de sistema usa sudo somente com autorização; um serviço de usuário mantém `systemctl --user`. O runtime Python e a conta operacional configurados continuam sendo usados nas consultas do setup.

## Uso

No setup, use **Daemon > Processo/Serviço do Daemon**. Os roteiros que oferecem reinício após salvar configurações usam o mesmo controle, assim como a retomada após uma atualização. O submenu de serviço mantém as ações específicas de instalação, conta e limites de recursos.

Para scripts, o CLI oferece as ações abaixo com `--onmyoji-root <instância>`. As ações de controle aceitam `--use-sudo` quando necessário:

```text
python -B -m onmyoji_daemon.cli --onmyoji-root <instância> daemon-status
python -B -m onmyoji_daemon.cli --onmyoji-root <instância> daemon-start
python -B -m onmyoji_daemon.cli --onmyoji-root <instância> daemon-restart
python -B -m onmyoji_daemon.cli --onmyoji-root <instância> daemon-stop
```

`daemon-status` retorna JSON com modo, atividade, disponibilidade RPC e registro do processo. Atividade do gerenciador e disponibilidade do supervisor são condições distintas: um serviço pode estar iniciando sem que seu RPC já responda. Os comandos legados `service-*` também verificam o resultado efetivo; `process-*` permanecem restritos à execução local.

## Confirmação da operação

O aceite de `systemctl` ou do Serviço do Windows não encerra a verificação. A inicialização precisa confirmar que o RPC autenticado e `process.json` identificam a mesma raiz, PID e execução. A execução deve ter carregado a versão atual do setup e a revisão salva do manifesto do daemon; no modo serviço, o PID também deve coincidir com o informado pelo gerenciador.

Cada inicialização gera um UUID, publicado no lock, em `process.json` e no RPC `ping`, sem expor o token de autenticação. O reinício exige um UUID novo e a saída dos processos anteriores. A comparação do UUID permite distinguir inicializações mesmo quando o sistema reutiliza um PID. A parada verifica a saída dos processos, a ausência de RPC e, para serviços, o estado do gerenciador. No Windows, o reinício aguarda `STOPPED` antes de solicitar a nova partida.

> [!IMPORTANT]
> Uma resposta do daemon antigo, uma versão desatualizada ou um RPC cujo PID difere do serviço produz erro. O controle não finaliza automaticamente um processo fora do serviço para ocultar o conflito. Consulte o status e os logs da instância, identifique e encerre o processo conflitante pelo sistema operacional antes de iniciar novamente o serviço.

Essa confirmação cobre o supervisor. A prontidão de módulos, como o Telegram, continua sendo verificada separadamente pelos respectivos roteiros. Reiniciar um módulo interno, como `restart gateway`, não reinicia o supervisor.
