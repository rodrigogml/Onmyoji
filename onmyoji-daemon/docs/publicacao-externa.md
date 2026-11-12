# Publicação externa compartilhada

O Daemon oferece dois serviços independentes para publicar listeners locais em
um hostname Cloudflare. `gateway` recebe HTTP e roteia pelo primeiro segmento do
path; `cloudflare-tunnel` administra a conexão Cloudflare e o processo
`cloudflared`. Mini Apps hospeda aplicações e o OAuth Broker como consumidor.

```text
Cloudflare → cloudflared → Gateway HTTPS → listener local
                                          ├─ /a/...     → Mini Apps
                                          ├─ /oauth/... → OAuth em Mini Apps
                                          └─ outro prefixo → serviço registrado
```

Há um Tunnel e um hostname por instância. Seu ingress contém uma única origem
Gateway e o fallback final `http_status:404`. Não existem regras Cloudflare nem
registros DNS por listener. Reikon não está implementado: um futuro serviço
participará pelo mesmo contrato de publicação.

## Configuração e operação

Abra **Daemon > Publicação externa**. O menu pode ser usado sem Mini Apps ativo.
Ele reúne configuração, diagnóstico, instalação, provisionamento, ciclo de vida,
listeners e migração legada. Gateway e Tunnel iniciam por escolha do operador;
a habilitação controla a retomada no próximo início do Daemon.

Use **!. Diagnóstico da publicação externa** para verificar pré-requisitos,
runtime, credencial KeePass, executável, serviços, origem HTTPS e conexão. O
diagnóstico funciona mesmo sem configuração ou serviços iniciados, apresenta
cada etapa e indica a ação para resolver cada pendência. Ele não instala,
inicia nem provisiona recursos. A opção 2 mantém acesso ao mesmo diagnóstico.
A última operação relevante mostra etapa, código de erro e próximo passo;
instalar outro binário não oculta uma falha de publicação. Pendências impedem
que o resultado anuncie a publicação como pronta. Mini Apps é opcional.
A presença da credencial no cofre não comprova suas permissões Cloudflare;
a confirmação remota acontece no provisionamento explícito.

Use **W. Wizard de publicação externa** para preparar a instância em sequência:
daemon, credencial, identificadores e hostname, executável, Gateway HTTPS,
provisionamento e retomada automática. O wizard pede confirmação antes de
provisionar Tunnel, ingress e DNS e apresenta o diagnóstico final. `X` cancela
a etapa atual; alterações já confirmadas, inclusive uma credencial salva no
KeePass, permanecem disponíveis para retomar o assistente.

A configuração começa pelo API Token no perfil de vault MCP: é possível
reutilizar uma senha existente, substituí-la ou criar a entrada com digitação
oculta. A sugestão para uma instância nova é
`<Shikigami>/Serviço/CloudflareTunnel:APIToken`. A configuração mantém somente
as referências do perfil e da entrada. Depois são solicitados:

- **Account ID:** identificador da conta Cloudflare, com 32 caracteres
  hexadecimais; não é o API Token.
- **Zone ID:** identificador do domínio administrado pela Cloudflare, também
  com 32 caracteres hexadecimais.
- **Hostname:** endereço público completo, como `tsuki.exemplo.com`, sem esquema,
  porta ou caminho. O túnel não exige informar o IP público do computador.

Os identificadores e o hostname são dados de configuração local em
`tunnel.toml`; o segredo permanece no KeePass. Antes de descobrir e publicar, a
consulta remota verifica se o Zone ID corresponde ao domínio do hostname (por
exemplo, `laveli.com.br` para `lavelinha.laveli.com.br`). Uma divergência
interrompe o processo sem modificar DNS, mesmo quando a listagem da zona não
contiver o hostname. Se a consulta da identidade da zona não for permitida,
a publicação é interrompida para evitar escrita em uma zona não verificada.
Consulte
[onde localizar os IDs](https://developers.cloudflare.com/fundamentals/account/find-account-and-zone-ids/)
e as [permissões do API Token para Tunnel e DNS](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel-api/).

| Dados locais, não versionados | Localização |
| --- | --- |
| Porta HTTPS do Gateway | `configs/daemon/services/gateway/config.toml` |
| Reservas de listeners | `configs/daemon/services/gateway/listeners.sqlite3` |
| Certificados privados do Gateway | `configs/daemon/services/gateway/tls/` |
| Identidade, origem e referências KeePass do Tunnel | `configs/daemon/services/cloudflare-tunnel/tunnel.toml` |
| Versões gerenciadas do executável | `configs/daemon/services/cloudflare-tunnel/bin/versions/`, salvo diretório personalizado |
| Checkpoints da migração | `configs/daemon/services/cloudflare-tunnel/migration.json` |
| Prévias e backups de recuperação remota | `configs/daemon/services/cloudflare-tunnel/publication-recovery/<preview_id>.json` |

O Gateway usa inicialmente `127.0.0.1:39390`. Sua porta é configurável e entra
em vigor após reinício. Mini Apps mantém o HTTPS local, normalmente em `39391`.
Alterar a origem do Gateway invalida a confirmação do ingress anterior; revise
e provisione novamente antes de considerar a publicação pronta.

O Tunnel seleciona um perfil KeePass existente e uma entrada com o API Token.
Edições parciais preservam Tunnel ID, nome e referências anteriores. O token da
API e o token de execução não são persistidos; o subprocesso recebe o token de
execução pelo ambiente, sem argumento de linha de comando. O setup não renomeia
entradas KeePass já configuradas.

O primeiro provisionamento valida o executável e oferece instalação quando ele
está ausente ou inválido. O assistente detecta a plataforma e mostra um diretório
gerenciado sugerido, permitindo outro caminho absoluto. O download usa a release
oficial, seu SHA-256 e uma execução `--version`; todo staging permanece em `tmp/`.
O serviço reporta separadamente falhas de plataforma, rede, integridade,
permissão e execução. Não há elevação silenciosa de privilégios.

**3. Instalar ou atualizar cloudflared** funciona antes de informar conta,
domínio ou credencial. Somente metadados do binário são gravados nesse caso;
isso não marca o Tunnel como configurado nem inicia uma conexão externa.
O diretório informado é inspecionado antes de qualquer confirmação de download:
o setup procura executáveis conhecidos, testa `--version` e compara com a
release estável oficial. Se encontrar mais de um executável válido, pede a
seleção pelo caminho e versão. Também é possível informar o caminho completo
do arquivo.

**Usar o executável existente** é a escolha inicial: registra o caminho absoluto
para que o serviço e os diagnósticos utilizem essa instalação, sem copiar,
baixar ou substituir o arquivo. Uma atualização disponível é oferecida como
ação separada e usa uma cópia gerenciada em `versions/`. Falha na consulta da
release é apresentada e não impede reutilizar uma instalação local validada.
Cada binário é revalidado pelo serviço antes de salvar sua seleção. A validação
de versão de um arquivo externo não comprova sua origem ou integridade oficial;
o SHA-256 oficial continua sendo exigido nas novas instalações gerenciadas.

O provisionamento aguarda a confirmação da origem HTTPS e da CA pelo Supervisor
antes de enviar qualquer alteração à Cloudflare.

### Confirmar o caminho público pelo navegador

Ao concluir a preparação do Tunnel, o setup oferece um teste por clique. Também
é possível repeti-lo em **Publicação externa > T. Testar acesso público**. O teste
exige o Gateway HTTPS pronto e o Tunnel configurado, provisionado e conectado, com o ingress
confirmado para a origem atual. Se faltar algum requisito, o setup explica a
pendência; concluir a configuração não equivale a confirmar acesso público.
Mini Apps não participa desse teste.

O setup mostra um link HTTPS no hostname configurado, com um path imprevisível
criado somente para aquela tentativa. Abra o link no navegador: a página informa
que a requisição chegou ao listener HTTPS do Gateway, e o setup recebe a mesma
confirmação. Somente o `GET` no path exato da sessão, dentro do prazo,
confirma a tentativa. DNS resolvido, processo ativo e respostas de
outras páginas não substituem esse acesso.

A tentativa dura até 120 segundos. `X` cancela a espera e encerra o teste;
expiração, cancelamento, conclusão ou encerramento do Gateway invalidam o
endpoint. Uma nova tentativa gera outro link. O endpoint usa um caminho sob
`/internal/publication-test/`, reservado ao Gateway, sem criar um listener
persistente ou ocupar prefixos das aplicações. O nonce do link e o estado da
tentativa ficam somente em memória, sem tokens Cloudflare na URL ou gravação
do link em logs HTTP.

> [!NOTE]
> O resultado confirma a chegada desta requisição ao Gateway da instância.
> O link usa o hostname configurado; os headers recebidos não autenticam a
> origem pública da requisição, pois podem ser reescritos pelo conector.
> Não testa autenticação nem funcionamento dos consumidores, como `/a/` e
> `/oauth/`. Regras Cloudflare Access, WAF, DNS ou conectividade podem impedir
> o clique de chegar; nesse caso o setup não declara o domínio validado.

O CLI oferece o mesmo contrato para acompanhamento manual:

```powershell
python -B -m onmyoji_daemon.cli --onmyoji-root C:/x/Onmyoji gateway publication-test start --confirm
python -B -m onmyoji_daemon.cli --onmyoji-root C:/x/Onmyoji gateway publication-test status <test_id>
python -B -m onmyoji_daemon.cli --onmyoji-root C:/x/Onmyoji gateway publication-test cancel <test_id>
```

`start` retorna o link e um `test_id` administrativo distinto do nonce público.
`status` retorna `waiting`, `confirmed`, `expired` ou `cancelled`; consultar o
estado não confirma o teste. Se a espera for abandonada, a expiração ainda
encerra o endpoint sem depender do setup. Os testes automatizados exercitam o
contrato localmente; a confirmação do domínio real exige o clique autorizado
na instalação em operação.

### Recuperar uma publicação após reinstalar

O wizard e **4. Preparar ou recuperar publicação** consultam DNS e Tunnels da
conta com a credencial KeePass selecionada. Um CNAME para `*.cfargotunnel.com`
permite descobrir a identidade do Tunnel sem arquivos da instalação anterior.
Sem DNS, a consulta usa o ID configurado ou procura o nome do Tunnel.

Quando encontra uma publicação compatível, o setup mostra hostname, nome e ID
do Tunnel, origem atual, nova origem e se o CNAME será reutilizado ou criado.
Se o ID local divergir do DNS, a prévia informa a troca do ID local que será
feita somente após confirmação.
Somente após a confirmação **Reutilizar este Tunnel e substituir a configuração
desta publicação? [s/N]** aplica a origem HTTPS e a CA do Gateway atual. O Tunnel
é preservado; o DNS compatível permanece intacto. Sem publicação anterior, a
criação de Tunnel, ingress e DNS exige sua própria confirmação.

A consulta valida leitura na API e acesso ao token de execução. Permissão de
escrita só é comprovada ao aplicar a operação: um token administrativo deve
permitir editar Cloudflare Tunnel na conta e DNS na zona. O token de execução
do conector não substitui esse API Token. Falhas HTTP indicam a operação e a
revisão necessária sem expor a credencial.

Cada prévia de reutilização recebe um ID e um snapshot local, sem tokens. Sua
aplicação vale por quinze minutos e compara novamente configuração local, CA,
DNS e ingress remoto; qualquer divergência exige nova consulta e confirmação.
Os backups continuam disponíveis para restauração após esse prazo. A consulta
nunca altera os recursos Cloudflare, mas pode salvar a prévia local.

> [!IMPORTANT]
> A recuperação automática aceita um Tunnel administrado remotamente e
> exclusivo deste hostname, sem filtros de caminho ou outras publicações.
> DNS externo, Tunnels locais e regras compartilhadas recebem diagnóstico
> acionável; o setup não apaga recursos alheios para resolver conflitos.

**9. Restaurar publicação anterior** lista backups de substituições iniciadas
ou concluídas e pede confirmação. Restaura o ingress anterior e as definições
locais, preservando o DNS preexistente; pode remover somente o CNAME criado
pela própria substituição. Mudanças externas posteriores impedem a restauração
automática. A disponibilidade restaurada depende da execução da origem antiga;
o rollback não reinstala aplicações nem configurações legadas.

Na adoção de uma publicação existente, o setup confirma a gravação remota com
releituras limitadas, permitindo apenas valores padrão documentados que a
Cloudflare acrescente ou omita em `originRequest`. Nunca considera equivalentes
alterações de hostname, serviço, `caPool`, desativação de verificação TLS ou
outras regras. Em divergências, exibe **somente caminhos de campos**, sem
valores sensíveis, preservando o backup e impedindo confirmação local. A
restauração aplica a mesma comparação conservadora.

As mesmas operações estão disponíveis no CLI:

```powershell
python -B -m onmyoji_daemon.cli --onmyoji-root C:/x/Onmyoji cloudflare-tunnel inspect-publication --wait
python -B -m onmyoji_daemon.cli --onmyoji-root C:/x/Onmyoji cloudflare-tunnel adopt-publication --preview-id <id-da-previa> --confirm --wait
python -B -m onmyoji_daemon.cli --onmyoji-root C:/x/Onmyoji cloudflare-tunnel rollback-publication --preview-id <id-da-previa> --confirm --wait
```

No MCP, use `cloudflare_tunnel.inspect_publication`,
`cloudflare_tunnel.adopt_publication` e `cloudflare_tunnel.rollback_publication`.
As duas escritas exigem `preview_id` e `confirm=true`. Todas retornam
`operation_id` para acompanhamento e nunca repetem a escrita após reinício.

Atualizações são explícitas. A versão anterior permanece disponível e o menu
permite restaurá-la. Um executável externo só é substituído como referência
após adoção explícita; seu arquivo original não é sobrescrito.

Exemplos no runtime Python da instância:

```powershell
python -B -m onmyoji_daemon.cli --onmyoji-root C:/x/Onmyoji start gateway
python -B -m onmyoji_daemon.cli --onmyoji-root C:/x/Onmyoji start cloudflare-tunnel
python -B -m onmyoji_daemon.cli --onmyoji-root C:/x/Onmyoji cloudflare-tunnel provision --confirm --wait
python -B -m onmyoji_daemon.cli --onmyoji-root C:/x/Onmyoji gateway listeners list
```

Provisionamento, configuração RPC e instalação retornam uma operação com
`operation_id`. Consulte `cloudflare-tunnel operation-status <id>`; a opção
`--wait` acompanha instalação, atualização, rollback do binário e provisionamento.
Uma operação interrompida não é reaplicada automaticamente após reinício.

O CLI configura localmente quando o serviço está parado. Com o Tunnel em
execução, a configuração passa pela mesma fila de operações e o CLI acompanha
seu resultado; uma operação ocupada é recusada sem escrita paralela no arquivo.

O MCP oferece `gateway.*` e `cloudflare_tunnel.*`. Escritas exigem
`confirm=true`; operações longas são acompanhadas por
`cloudflare_tunnel.operation_status`. A integração `cloudflare.*` de DNS geral
continua separada. APIs antigas `mini-apps.tunnel.*`, ferramentas
`mini_apps.tunnel_*`, CLI `mini-apps tunnel` e administração HTTP do Tunnel em
Mini Apps foram removidas.

## Contrato de listeners

O consumidor deve ser registrado no Daemon como publicador HTTP. Ele expõe
`<namespace>.publication.describe` com a versão do contrato e seus listeners:
prefixo, endpoint local, prontidão e CA quando usar HTTPS. A identidade não vem
de um campo livre enviado pelo consumidor: o Supervisor atribui o proprietário
e a geração a partir do processo que consultou e valida a porta autorizada.
Somente o Supervisor sincroniza o registro privado do Gateway.

Cada prefixo é um segmento ASCII canônico em minúsculas e pertence a um único
serviço. Não há correspondência textual parcial: `app` não captura
`application`. O Gateway preserva integralmente o path aprovado e a query;
não remove o primeiro segmento. Caminhos ambíguos, traversal e codificações
estruturais alternativas são rejeitados. `internal` é reservado.

| Estado | Reserva do prefixo | HTTP |
| --- | --- | --- |
| Habilitado e disponível | Mantida | Encaminha ao proprietário |
| Habilitado e indisponível | Mantida | `503` |
| Desabilitado | Mantida | `404` |
| Removido ou inexistente | Livre | `404` |

Conflitos são transacionais: nenhuma rota do conjunto é alterada se houver
prefixo pertencente a outro serviço. Reiniciar o Gateway conserva as reservas,
mas exige nova confirmação dos destinos da geração atual. Parada, demora no RPC
ou desaparecimento de declaração nunca transferem propriedade nem removem uma
reserva automaticamente.

A remoção grava uma supressão persistente para o proprietário antigo, impedindo
que sincronizações recriem o listener. Restaurar é uma ação explícita e volta a
verificar conflitos. Outro proprietário pode assumir um prefixo já removido;
isso não autoriza o antigo a recuperá-lo automaticamente.

## HTTP, TLS e disponibilidade

O proxy suporta HTTP, streaming e WebSocket. O limite inicial de corpo é 64 MiB,
inclusive para upload chunked. A conexão ao destino tem limite de cinco segundos
e a inatividade HTTP de 200 segundos, compatível com callbacks OAuth. Streams e
WebSockets ativos não recebem um prazo total fixo.

Headers de aplicação, `Authorization`, cookies e `Set-Cookie` duplicados são
preservados. O Gateway não compartilha cookies entre clientes, não segue
redirects do backend e não descomprime suas respostas. Headers hop-by-hop são
removidos; `Host` usa a autoridade configurada, e headers de encaminhamento não
são aceitos como identidade do cliente. Logs HTTP não registram query strings,
cookies ou credenciais.

Os trechos cloudflared → Gateway e Gateway → Mini Apps validam TLS por CAs
privadas explícitas. Não há `noTLSVerify` global. Autenticação e autorização de
negócio permanecem no serviço destinatário, incluindo Basic, claim, sessões,
segredos de callback, `state` OAuth e PKCE.

`running` indica processo ativo; conexão confirmada e ingress correspondente à
origem atual são estados separados. Mini Apps recebe do Supervisor um resumo
com origem pública configurada, disponibilidade, `checked_at`, `confirmed_at`
(última sincronização confirmada para a geração atual) e erro sanitizado.
`public_url` configurada não
prova acesso externo: consulte também `public_available` e o diagnóstico. Mini
Apps continua local quando a publicação falha; OAuth remoto exige o caminho
público pronto. URLs existentes e callbacks cadastrados são preservados.

Uma morte inesperada do conector aciona a recuperação do serviço habilitado pelo
Supervisor. A árvore de processos é contida para impedir conectores órfãos ou
duas gerações simultâneas. Paradas explícitas encerram normalmente.

## Migração da configuração antiga

> [!IMPORTANT]
> A migração é explícita e exige uma janela de manutenção. Atualize o código e
> reinicie o Daemon para registrar os novos serviços antes de executá-la.

Use **Publicação externa > 8. Migrar configuração legada**, ou
`publication-migrate inspect|apply|rollback`. Aplicação e rollback exigem
`--confirm`. A configuração original de Mini Apps permanece intacta.

O pré-flight exige Tunnel legado provisionado, configuração de destino compatível,
ingress exclusivo do hostname, DNS compatível e executável cloudflared validado
por `--version` antes de parar o proprietário antigo. Não cria outro Tunnel nem
substitui configurações divergentes. Conta, zona, Tunnel ID, nome, hostname,
referências KeePass e executável são preservados.

Após encerrar o proprietário antigo e confirmar a saída do seu conector, a
migração prepara Mini Apps e Gateway, espera as reservas `a` e `oauth` e testa
HTTPS/encaminhamento local. Só então troca o ingress e inicia o novo proprietário.
A conclusão exige o conector ativo, ingress confirmado, DNS compatível e conexão estabelecida. A troca do ingress só é aplicada se a configuração remota ainda corresponder ao snapshot validado no diagnóstico.
Nenhum hostname ou callback cadastrado no provedor é alterado.

Falhas deixam um manifesto com o estágio e o estado anterior. Não há repetição
automática sobre uma transição incompleta. O rollback recusa alterações remotas
feitas fora desta migração; ele para os serviços novos, restaura o ingress salvo
e remove somente um DNS criado pela própria migração,
após verificar sua identidade e conteúdo. Uma configuração legada alterada após
a migração precisa ser restaurada antes do rollback. Se uma criação DNS não
retornou seu ID, a propriedade precisa ser verificada pelo operador antes da
remoção; o rollback não adota um registro desconhecido. Uma nova aplicação
explícita após rollback arquiva o manifesto anterior em `migration-history/`.

> [!WARNING]
> O rollback da infraestrutura não reintroduz o controlador removido do código.
> Reinstale a versão anterior e reinicie o Daemon para reativar o proprietário
> legado. O resultado informa `rollback_requires_previous_version` explicitamente.

## Validação local

Execute da raiz `python -B -m pytest -c pytest.ini`. Os testes cobrem conflitos,
propriedade, retomada, compatibilidade HTTP/OAuth, instalação verificada,
recuperação e migração/rollback sem chamar Cloudflare real. Uma verificação
externa real continua dependendo da configuração autorizada da instância;
não faz parte da execução automática dos testes.

Os contratos exercitados e as pendências preexistentes estão registrados em
[Validação da publicação externa](validacao-publicacao-externa.md).
