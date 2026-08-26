# Configuração

Cada instância Onmyōji mantém `configs/eccovox.toml` local, fora do versionamento, e cada chamada informa também `--profile`. O modelo `configs/eccovox.toml.model` contém todas as chaves aceitas.

`server.base_url` deve ser uma URL HTTP de loopback, sem caminho, credenciais, query ou fragmento. O wrapper rejeita hosts remotos e HTTPS para impedir que áudio, transcrições ou texto sejam enviados a terceiros por engano. `request_timeout_seconds` deve estar entre 1 e 300.

`limits.max_audio_bytes` limita os arquivos enviados para STT. `limits.max_text_characters` limita o texto enviado para TTS. Os limites do perfil não substituem os limites próprios do runtime.

`readable_roots` e `writable_roots` são arrays TOML de diretórios absolutos. STT só pode ler arquivos contidos nas raízes de leitura. TTS só pode gravar arquivos contidos nas raízes de escrita; diretórios pai precisam existir. Não configure uma raiz ampla sem necessidade.

O serviço EccoVox desta skill não requer token ou senha. Não acrescente uma seção de credenciais ao perfil. A restrição de acesso ao serviço é a execução local em loopback e as ACLs do sistema operacional.

