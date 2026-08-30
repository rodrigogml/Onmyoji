# Wrappers de domínio

Cada wrapper fixa seu namespace e mantém um manifesto versionado no próprio projeto. O wrapper valida políticas de negócio antes de chamar o memory-store; o núcleo somente valida estrutura e integridade.

Declare somente atributos que precisam ser filtrados, ordenados ou validados. Use memória textual para evidência e decisões; guarde o identificador do texto no registro estruturado quando for necessário relacioná-los. Não use campos JSON como substituto de atributos consultados repetidamente.

Exemplo de operação de wrapper: aplicar migrations no provisionamento, validar a regra empresarial, enviar `record.upsert` com chave única declarada e, em seguida, registrar uma nota `text.add` com fonte e confiança.
