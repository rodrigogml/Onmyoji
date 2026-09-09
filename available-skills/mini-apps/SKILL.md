---
name: mini-apps
description: Publica e administra Mini Apps Python no Gateway local do Onmyoji.
---

# Mini Apps

Use `scripts/mini_apps.py` para criar, publicar, consultar, atualizar,
recarregar, despublicar e excluir Mini Apps. O diretório informado deve estar
no workspace configurado do Shikigami; o agente mantém todos os arquivos da
aplicação e o Gateway mantém somente a publicação.

Envie `create --request` com JSON contendo `root_path`, `entrypoint` e `auth`.
Use `auth.type` igual a `claim` ou `basic`; senhas Basic só existem no comando
de criação e não são retornadas pelo Gateway.
