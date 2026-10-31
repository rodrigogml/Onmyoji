# OCR no MCP local

`ocr.*` reconhece texto em imagens locais e oferece análise de layout e tabelas. Também fornece o serviço interno usado por [PDF](mcp-pdf.md). Não utiliza o transporte MCP para reconhecer cada página e não depende do daemon.

## Perfis e instalação

Use **MCP > Integrações MCP > OCR**. O roteiro oferece entrada, saída e trabalho em `<workspace>/.onmyoji/ocr/`, português e inglês (`por;eng`) e 300 DPI. Enter aceita os valores mostrados. Caminhos personalizados devem permanecer no workspace. A edição associa cada atributo à opção que permite alterá-lo.

A configuração local é `configs/mcp/ocr.toml`. Cada perfil define engine, idiomas, DPI, executável e dados de idioma do Tesseract, diretórios autorizados e modelos avançados. `ocr.get_capabilities` diagnostica motores, versões e modelos; `ocr.list_languages` lista os idiomas disponíveis no Tesseract instalado.

Há três opções de preparação pelo setup:

| Nível | Preparação e finalidade |
| --- | --- |
| Básico | Bibliotecas PDF/OCRmyPDF no runtime privado; Tesseract e seus modelos de idioma são dependências nativas locais. |
| Avançado CPU/GPU | Ambiente Python separado com PaddleOCR/PP-StructureV3 e modelos preparados explicitamente; GPU exige instalação compatível e dispositivo disponível. |
| PDF/A | Bibliotecas de suporte e diagnóstico de Ghostscript/veraPDF; configure executáveis e perfil ICC no perfil PDF. |

Tesseract, Ghostscript e veraPDF são programas nativos. O diagnóstico deve indicar sua ausência; instalar pacotes Python não significa que esses programas foram instalados. No Windows, configure os executáveis completos quando eles não estiverem no PATH; no Linux utilize a instalação administrada pela distribuição. O ambiente avançado e seus modelos ficam em `configs/mcp/ocr/`, sem documentos de atividade. Durante o processamento, o backend avançado exige modelos previamente preparados e não baixa arquivos.

## Uso e qualidade

`ocr.recognize` aceita PNG/JPEG e outros formatos reconhecidos pelo Pillow, inclusive TIFF multipágina. Retorna texto, frames, blocos, caixas em pixels e confiança quando a engine oferece essa informação. `ocr.analyze_layout` usa o backend avançado para leitura estruturada e tabelas. O schema da ferramenta informa as opções de saída e processamento.

As chamadas retornam `job_id`. Consulte `ocr.get_job` e `ocr.get_job_result`; `ocr.cancel_job` cancela e `ocr.list_jobs` consulta o histórico. IDs e resultados são limitados ao perfil. Exportações exigem `confirm=true`, `output_path` novo e raiz de escrita autorizada. Resultados extensos usam o contrato de paginação descrito na documentação PDF.

O módulo PDF seleciona um perfil OCR e reutiliza esses reconhecedores em imagens temporárias de suas páginas, sem ampliar as permissões de arquivo do perfil PDF. `pdf.make_searchable` usa OCRmyPDF para produzir uma nova cópia pesquisável, respeitando fontes assinadas e limites.

> [!NOTE]
> Idioma correto, resolução e qualidade da imagem afetam os resultados. Confiança é um sinal do motor, não uma certificação de exatidão. Manuscritos, fórmulas e tabelas complexas podem exigir revisão humana. Não há promessa universal de precisão.

A suite de desenvolvimento inclui imagens sintéticas com texto de referência e métricas de erro por reconhecimento quando o motor nativo estiver disponível. Testes de contratos usam respostas controladas; diagnósticos distinguem ausência de engine de uma falha funcional. A homologação dos binários/modelos deve ser executada em cada plataforma de destino antes de distribuição.
