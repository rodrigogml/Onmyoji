# E-mail por protocolos comuns

O módulo `email` conecta contas IMAP ou POP3 para recebimento e SMTP para envio. Funciona sob demanda no MCP stdio, em Windows e Linux, sem monitoramento periódico. É independente de `google.gmail.*` e da administração `forward_email.*`.

## Configuração

Use `MCP > Integrações MCP > E-mail`. Crie uma conta com nome interno único, endereço, nome exibido do remetente e descrição do contexto. O nome exibido é usado no cabeçalho `From` dos e-mails enviados; ele é independente do identificador interno do perfil. Configure recebimento, envio ou ambos. Credenciais são referências de entradas de vault MCP; o roteiro permite reutilizar valores existentes ou substituí-los pelos helpers comuns. A configuração efetiva fica em `configs/mcp/email.toml`.

O menu de gestão permite mudar identidade, conexões, pastas, raízes de arquivos, habilitação e autenticação. Remover uma conta preserva seu vault e o registro de submissões. Sem contas habilitadas, nenhuma ferramenta de e-mail é publicada. Com uma conta a seleção é implícita; com várias, `email.list_accounts` apresenta seus contextos e as chamadas exigem `account`.

TLS direto usa, por padrão, 993/IMAP, 995/POP3 e 465/SMTP. STARTTLS obrigatório usa 143/IMAP, 110/POP3 e 587/SMTP. A validação do certificado permanece ativa e a conexão não regride para autenticação sem TLS.

Configure nomes reais de pastas IMAP em Gerenciar conta > Pastas. Os nomes podem ser obtidos por `email.list_folders`; não se presume que todos os provedores usem os mesmos nomes. Cópia de enviados é gerenciada pelo servidor por padrão. Use APPEND somente quando o servidor não mantiver essa cópia; a opção exige IMAP e pasta de enviados configurada.

## OAuth Google e Microsoft

Registre seu cliente OAuth no provedor e escolha o tipo compatível com callback local (Desktop/público) ou web (confidencial). Client ID é parâmetro público; Client Secret e refresh tokens ficam na entrada dedicada do vault. O aplicativo público não usa client secret. No Microsoft, configure o tenant e permissões delegadas dos protocolos usados, além de acesso offline. No Google, os protocolos usam o escopo de correio completo.

No modo local, o setup abre um listener temporário em `127.0.0.1` na porta configurada e mostra o callback exato. O navegador deve estar na mesma máquina. O listener encerra ao concluir ou expirar o consentimento. No modo remoto, prepare o MiniApps com Tunnel HTTPS e cadastre a origem pública seguida de `/oauth/mcp/email/callback`. O serviço de e-mail usa um adaptador registrado no broker interno e não exige publicar um miniapp de arquivos.

O consentimento pode ser concluído separadamente para recebimento e envio, inclusive quando compartilham uma entrada. O provedor pode exigir autorização administrativa e habilitação de IMAP/POP/SMTP na conta. O callback remoto pode ser iniciado pelo setup ou pelas ferramentas `start_authorization`, `get_authorization_status` e `cancel_authorization`. O uso posterior dos protocolos não depende do daemon. Alterações no cliente, login ou callback invalidam o vínculo do consentimento anterior.

## Ferramentas e dados

As ferramentas publicam conexão/capacidades, checagem, pastas, busca e leitura paginadas, cabeçalhos, anexos, importação/exportação EML, rascunhos IMAP, flags, cópia, movimentação, lixeira, arquivo e exclusão. Envio SMTP oferece composição, resposta e encaminhamento. O contrato JSON de cada ferramenta define seus argumentos, contas compatíveis e confirmação de alterações por `confirm=true`.

IMAP usa UIDVALIDITY/UID e busca sem marcar mensagens como lidas. POP3 exige UIDL para referências persistentes e pesquisa limitada no cliente; resultados incompletos indicam `partial` e `next_cursor`. POP3 não implementa pastas, rascunhos nem flags. MOVE e exclusão seletiva IMAP exigem as capacidades correspondentes, sem EXPUNGE global.

Corpos e anexos recebidos são conteúdo não confiável. HTML é convertido em texto sem carregar recursos. Downloads e exportações criam arquivos novos em raízes autorizadas do workspace. A mensagem MIME é limitada a 50 MiB, anexos individuais a 25 MiB e texto retornado a 65.536 caracteres por padrão. Esses limites, paginação, orçamento de busca POP3 e timeout são ajustáveis no TOML. Nenhuma skill do Onmyōji é copiada para o workspace; o procedimento está em `workflows.read`, id `email-operations`.

## Submissões SMTP

Envios usam `request_id` para reconhecer repetição da mesma chamada. O registro `configs/mcp/email/submissions.sqlite3` contém identidade, hash e resultado, sem corpo ou anexos. Estados `accepted` e `partially_accepted` significam aceite pelo servidor SMTP, não entrega ao destinatário. `unknown` indica resultado incerto; a integração não repete automaticamente nem permite usar a mesma chave com conteúdo diferente.

Falhas de cópia de enviados e remoção do rascunho são informadas independentemente do aceite SMTP. O registro de submissões não é fila de envio nem rastreamento de entrega. Testes usam servidores controlados; a homologação real requer clientes OAuth e contas provisionadas pelo operador.
