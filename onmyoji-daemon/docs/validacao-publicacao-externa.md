# Validação da publicação externa

Esta entrega desacopla Cloudflare Tunnel de Mini Apps. A validação local usa
backends de teste e subprocessos reais do Daemon, sem alterar DNS, credenciais
ou publicação da instância em uso. O procedimento operacional está em
[Publicação externa](publicacao-externa.md).

## Contratos exercitados

| Contrato | Evidência na suíte do Daemon |
| --- | --- |
| Identidade atribuída pelo Supervisor, portas autorizadas e descarte de resultados de gerações antigas | `test_publication_supervisor.py` |
| Reservas transacionais, conflitos concorrentes, desabilitação, remoção persistente e restauração | `test_gateway_registry.py` |
| Dois consumidores sob uma origem, autenticação própria, preservação de path/query e bloqueio de `/internal/` | `test_gateway_http.py` |
| TLS validado por CA explícita, isolamento de cookies, headers duplicados, redirects e compressão | `test_gateway_http.py` |
| Streaming, WebSocket, timeout antes dos headers, conexão interrompida após headers e limite de upload chunked inclusive com resposta antecipada | `test_gateway_http.py` |
| URLs locais disponíveis sem publicação externa e callbacks BTG/E-mail durante RPC OAuth ocupado | `test_miniapps_publication.py`, `test_miniapps.py` |
| Namespaces RPC/MCP/CLI, erros estruturados e remoção das APIs antigas | `test_publication_rpc.py`, `test_publication_admin.py`, `test_publication_stdio.py` |
| Comandos do teste público temporário, confirmação explícita, identidade da tentativa e pré-requisitos com erro estruturado | `test_publication_test_cli.py` |
| Link temporário por tentativa, confirmação somente pelo path correto, expiração, cancelamento e limpeza sem Mini Apps | `test_gateway_publication_test.py` |
| Link no setup, pré-requisitos, espera com X/Ctrl+C, confirmação no limite do prazo e falhas sanitizadas | `tests/test_setup_publication_test.py` na raiz |
| Configuração parcial, conflitos DNS/ingress, operação assíncrona e interrupção sem repetição automática | `test_cloudflare_tunnel.py`, `test_cloudflare_tunnel_service.py` |
| Release oficial, asset existente, SHA-256, permissões, atualização e rollback preservando a versão anterior | `test_cloudflare_tunnel_installer.py` |
| Instalação sem conta/token, metadados parciais distintos de Tunnel configurado e diagnóstico sem serviços ou escritas | `test_cloudflare_tunnel_installer.py`, `test_cloudflare_tunnel_service.py`, `test_publication_diagnostics.py` |
| Token somente no ambiente restrito, falha recuperável, parada intencional e encerramento de descendentes | `test_cloudflare_tunnel.py`, `test_cloudflare_tunnel_service.py`, `test_service_process_group.py` |
| Migração repetida, validação do executável antes do corte, alterações concorrentes, falhas intermediárias e rollback explícito | `test_publication_migration.py` |
| Menu independente de Mini Apps e convenções de apresentação do setup | `tests/test_setup_onmyoji.py` na raiz |
| Wizard com KeePass antes dos IDs, validação/repetição de entradas, cancelamento e confirmação da origem antes do provisionamento | `tests/test_setup_onmyoji.py` na raiz |

O listener `reikon` existe somente nos testes de coexistência. A suíte exercita
Job Object no Windows; a supervisão de morte do processo pai POSIX possui teste
com ambiente simulado, e a implementação de grupos POSIX é selecionada em Linux.

## Pendências preexistentes

Resultado da execução completa de `python -B -m pytest -c pytest.ini`:
**1.384 aprovados, 12 ignorados e oito falhas preexistentes**. Após o último
ajuste de serialização da configuração pelo CLI, o conjunto de CLI, administração
de publicação e setup foi executado novamente: **81 aprovados**.

Em 4 de novembro de 2026, os oito casos abaixo também falharam em uma cópia
isolada de `HEAD` (`f8af268938e1bdb6f13b4fdf6cf88dba54b8fd8e`), extraída em
`tmp/`. Permanecem separados da aceitação dos contratos novos.

| Grupo | Casos e causa observada |
| --- | --- |
| Runtime: quatro casos | `test_runtime_install_creates_private_environment`, `test_external_runtime_is_not_changed_without_install_permission`, `test_daemon_python_uses_selected_external_runtime`, `test_miniapps_uses_private_daemon_python_when_available`: expectativas anteriores ao runtime central, incluindo diretório e mensagens antigas. |
| E-mail: dois casos | `test_real_protocol_clients_use_validated_tls_and_preserve_messages[tls-imap]` e `[starttls-imap]`: `IMAPClient` ausente no Python usado para executar os testes. |
| Telegram: um caso | `test_file_is_limited_to_workspace_and_retained_by_destination`: a restrição é aplicada, mas o teste espera a mensagem anterior. |
| OCR: um caso | `test_model_cache_rejects_windows_paths_that_exceed_the_legacy_limit`: o caminho produzido com `tmp/pytest` não alcança o limite que o teste presume. |

Execute a suíte da raiz com `python -B -m pytest -c pytest.ini`. Temporários do
Python são isolados por execução dentro de `tmp/`, permitindo testes focados
paralelos com `--basetemp` distintos sem remover arquivos de outra execução.

## Referências externas do contrato

- [Parâmetros da origem Cloudflare, incluindo `caPool`](https://developers.cloudflare.com/tunnel/reference/origin-parameters/).
- [Parâmetros de execução, incluindo `TUNNEL_TOKEN`](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/run-parameters/).
- [Metadados de assets de releases e digest](https://docs.github.com/en/rest/releases/assets).

> [!NOTE]
> A migração real é uma ação explícita do operador, com janela de manutenção.
> Os testes não provisionam recursos Cloudflare nem executam esse corte.
