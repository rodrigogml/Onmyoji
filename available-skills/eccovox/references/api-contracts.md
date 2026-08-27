# Contrato do wrapper

O wrapper lê uma única requisição JSON do stdin e escreve uma única resposta JSON no stdout. Toda requisição exige `version: 1` e `operation`.

## Consultar estado

```json
{"version":1,"operation":"health.get"}
```

O resultado contém apenas o estado, versão e capacidades publicadas pelo runtime.

## Transcrever áudio

```json
{"version":1,"operation":"stt.transcribe","audio_path":"C:\\staging\\mensagem.ogg","language":"pt-BR","profile":"balanced"}
```

Retorna `data.text`, e quando publicados pelo runtime `language`, `confidence`, `duration_millis` e `metadata`. O caminho nunca é ecoado na resposta.

## Sintetizar fala

```json
{"version":1,"operation":"tts.synthesize","text":"Olá.","output_path":"C:\\staging\\resposta.mp3","response_format":"mp3","confirm":true}
```

`confirm: true` é obrigatório porque a operação escreve ou substitui `output_path`. Formatos suportados são `mp3`, `wav`, `opus` e `flac`; a extensão do arquivo precisa corresponder ao formato. A resposta retorna o caminho de saída e o content type somente após sucesso.

## Erros

Erros têm o formato `{ "version": 1, "ok": false, "error": { "code": "...", "message": "..." } }`. O wrapper converte erros HTTP em códigos seguros, nunca retorna cabeçalhos, corpo bruto ou conteúdo enviado.

