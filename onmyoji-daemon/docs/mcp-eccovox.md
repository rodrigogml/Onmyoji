# EccoVox no MCP interno

O módulo `eccovox` oferece transcrição de áudio e síntese de fala pelo runtime local EccoVox. O MCP atua como cliente desse runtime; criar um perfil não instala nem inicia o EccoVox.

## Configuração

Use **MCP > Integrações MCP > EccoVox**. Cada perfil tem nome, descrição de contexto, habilitação, endpoint HTTP local e raízes autorizadas de leitura e escrita. O endpoint sugerido é `http://127.0.0.1:8870`; somente endereços de loopback são aceitos.

A configuração fica em `configs/mcp/eccovox.toml`. O menu permite alterar descrição, endpoint e raízes, testar a conexão, habilitar, desabilitar e remover o perfil. Esta integração local não utiliza credenciais de vault.

Sem perfil habilitado, nenhuma ferramenta é publicada. Um único perfil é selecionado implicitamente; com vários, `eccovox.list_profiles` apresenta os contextos e as chamadas exigem `profile`.

## Ferramentas e dados

| Ferramenta | Comportamento |
| --- | --- |
| `eccovox.get_health` | Consulta saúde, versão e capacidades informadas pelo runtime. |
| `eccovox.transcribe_audio` | Lê `audio_path` em uma raiz autorizada e retorna texto e metadados disponíveis. Aceita `language` e `runtime_profile` opcionais. |
| `eccovox.synthesize_speech` | Converte `text` em áudio no `output_path` autorizado. Aceita formato, voz, idioma, velocidade e perfil do runtime; exige `confirm=true`. |

Na transcrição, os formatos aceitos são WAV, MP3, M4A, MP4, OGG, Opus, WebM e FLAC. Na síntese, são MP3, WAV, Opus e FLAC; a extensão de saída deve coincidir com o formato solicitado. O perfil MCP escolhe a conexão e as permissões locais; `runtime_profile` seleciona um perfil do próprio EccoVox.

## Arquivos e uso pelo Telegram

O serviço confere os caminhos resolvidos contra `readable_roots` e `writable_roots` e aplica os limites configurados de áudio, texto e timeout. A síntese pode substituir o arquivo de saída existente dentro da raiz autorizada.

O [Gateway Telegram](Telegram.md) também usa esse serviço internamente para voz. A escolha de perfil e a habilitação de respostas em áudio pertencem à configuração do Telegram; permissões de envio de fotos e documentos são independentes da integração EccoVox. Ao configurar voz, o setup autoriza o staging do Telegram nas raízes do perfil escolhido.
