# Operações

`list` aceita `path` opcional. `list.totp` aceita `path` opcional e enumera TOTPs por exportação XML em memória. `read` exige `path` e `field`. `add` e `edit` aceitam os campos `username`, `password`, `url` e `notes`. `copy` exige `source_path`, `destination_path` e `field`.

Operações de anexos usam `path` da entrada e `name`; importação e exportação também usam `file_path`. Os caminhos de arquivo devem estar abaixo de `allowed_attachment_roots` do perfil.
