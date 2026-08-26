# Configuração

Cada projeto consumidor deve manter um perfil INI próprio, fora deste repositório, e sempre passá-lo com `--config`. O modelo em `configs/eccovox.example.ini` contém todas as chaves aceitas.

`server.base_url` deve ser uma URL HTTP de loopback, sem caminho, credenciais, query ou fragmento. O wrapper rejeita hosts remotos e HTTPS para impedir que áudio, transcrições ou texto sejam enviados a terceiros por engano. `request_timeout_seconds` deve estar entre 1 e 300.

`limits.max_audio_bytes` limita os arquivos enviados para STT. `limits.max_text_characters` limita o texto enviado para TTS. Os limites do perfil não substituem os limites próprios do runtime.

`paths.readable_roots` e `paths.writable_roots` são listas de diretórios absolutos, uma raiz por linha. STT só pode ler arquivos contidos nas raízes de leitura. TTS só pode gravar arquivos contidos nas raízes de escrita; diretórios pai precisam existir. Não configure uma raiz ampla sem necessidade.

O serviço EccoVox desta skill não requer token ou senha. Não acrescente uma seção de credenciais ao perfil. A restrição de acesso ao serviço é a execução local em loopback e as ACLs do sistema operacional.

