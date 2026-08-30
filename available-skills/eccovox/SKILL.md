---
name: eccovox
description: Usa um serviço EccoVox local para verificar disponibilidade, transcrever arquivos de áudio (STT) e gerar arquivos de fala (TTS), usando um perfil explícito e sem enviar conteúdo para hosts externos.
---

# EccoVox

Use esta skill quando for necessário transcrever um arquivo de áudio local ou sintetizar fala por meio de um runtime EccoVox local. Antes de STT ou TTS, consulte `health.get` quando a disponibilidade do serviço não for conhecida.

Execute o wrapper com um perfil explícito e passe exatamente uma requisição JSON pelo stdin:

```powershell
python scripts/eccovox.py --config <CODEX_HOME>/configs/eccovox.toml --profile <perfil>
```

O arquivo real fica em `<CODEX_HOME>/configs/eccovox.toml`, criado a partir de `configs/eccovox.toml.model`. O stdout contém exclusivamente uma resposta JSON versão 1. Não inclua áudio, texto a ser sintetizado, transcrições ou dados pessoais em logs, mensagens de erro ou argumentos de linha de comando.

Operações disponíveis:

- `health.get`: informa o estado seguro do runtime e de STT/TTS.
- `stt.transcribe`: recebe `audio_path` e, opcionalmente, `language` e `profile`; o arquivo deve estar sob `paths.readable_roots`.
- `tts.synthesize`: recebe `text`, `output_path` e opcionalmente `voice`, `language`, `profile`, `response_format` e `speed`; a saída deve estar sob `paths.writable_roots`.

Exemplos de requisição estão em [api-contracts.md](references/api-contracts.md). Configure o endpoint local e as raízes autorizadas conforme [configuration.md](references/configuration.md). As operações não alteram o runtime; TTS somente cria ou substitui o arquivo de saída indicado e exige `confirm: true`.

O wrapper aceita somente `http://` em loopback (`127.0.0.1`, `localhost` ou `::1`) e nunca segue redirecionamentos. Confirme o destino e a extensão do arquivo antes de solicitar TTS.

