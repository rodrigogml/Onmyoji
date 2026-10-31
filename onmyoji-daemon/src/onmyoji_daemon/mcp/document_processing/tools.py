"""Contratos fechados e despacho assíncrono dos módulos PDF e OCR."""
from __future__ import annotations

import atexit
from copy import deepcopy

from .configuration import load_configuration
from .contracts import DocumentError
from .jobs import Jobs


def string(description=""):
    return {"type": "string", "minLength": 1, "maxLength": 4096, "description": description}


def enum(*values):
    return {"type": "string", "enum": list(values)}


def array(items, minimum=0, maximum=2000):
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}


def object_schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


NUMBER = {"type": "number"}
BOOLEAN = {"type": "boolean"}
POSITIVE = {"type": "integer", "minimum": 1}
PATH = string("Caminho absoluto dentro das raízes autorizadas do workspace.")
RECT = {"type": "array", "items": NUMBER, "minItems": 4, "maxItems": 4, "description": "[x0,y0,x1,y1] em pontos, origem superior esquerda da página visível após rotação."}
COLOR = {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1}, "minItems": 3, "maxItems": 3}
PASSWORD = object_schema({"credential_profile": string(), "credential_entry_path": string(), "field": enum("password")}, ["credential_profile", "credential_entry_path"])
INPUT = {"input_path": PATH, "password_ref": PASSWORD}
SELECTION = {"pages": string("Páginas a partir de 1, por exemplo 1-3,7,10-12."), "region": RECT}
OUTPUT = {"output_path": PATH, "allow_signature_invalidation": BOOLEAN}
OCR_OPTIONS = {"engine": enum("tesseract", "paddleocr"), "languages": array(string(), 1, 20), "dpi": {"type": "integer", "minimum": 72, "maximum": 600}, "rotate": BOOLEAN, "deskew": BOOLEAN}
FIELDS = {"type": "object", "maxProperties": 5000, "additionalProperties": {"type": ["string", "boolean", "number", "null"]}}
REGION = object_schema({"page": POSITIVE, "rect": RECT}, ["page", "rect"])

# description, extra fields, mandatory fields; action-specific validation is performed before enqueueing.
PDF_SPECS = {
    "inspect": ("Inspeciona estrutura e recursos do PDF.", {}, []),
    "extract_text": ("Extrai texto com página, coordenadas e proveniência; OCR não é certeza de exatidão.", {**SELECTION, **OCR_OPTIONS, "mode": enum("native", "ocr", "auto"), "format": enum("text", "json", "markdown"), "output_path": PATH}, []),
    "search": ("Localiza texto ou expressão regular e retorna regiões por página.", {**SELECTION, "query": string(), "regex": BOOLEAN, "case_sensitive": BOOLEAN}, ["query"]),
    "extract_tables": ("Extrai células e regiões de tabelas digitais ou digitalizadas.", {**SELECTION, **OCR_OPTIONS, "mode": enum("native", "ocr", "auto"), "strategy": enum("lines", "lines_strict", "text"), "format": enum("json", "csv", "xlsx"), "output_path": PATH}, []),
    "render_pages": ("Renderiza páginas, recortes, miniaturas ou folha de contato.", {**SELECTION, "format": enum("png", "jpeg"), "dpi": {"type": "integer", "minimum": 36, "maximum": 600}, "contact_sheet": BOOLEAN, "output_directory": PATH, "output_path": PATH}, []),
    "extract_images": ("Extrai imagens incorporadas preservando a fonte.", {**SELECTION, "output_directory": PATH}, ["output_directory"]),
    "select_pages": ("Extrai, exclui, reordena, duplica ou insere páginas em um PDF novo.", {**SELECTION, **OUTPUT, "action": enum("extract", "delete", "reorder", "duplicate", "insert"), "page_order": array(POSITIVE, 1), "insert_path": PATH, "insert_at": POSITIVE}, ["output_path"]),
    "merge": ("Mescla ou intercala PDFs na ordem informada.", {**OUTPUT, "password_ref": PASSWORD, "input_paths": array(PATH, 2, 100), "action": enum("concat", "interleave")}, ["input_paths", "output_path"]),
    "split": ("Divide por intervalos ou marcadores e publica somente saídas completas.", {"ranges": array(string(), 1), "action": enum("ranges", "bookmarks"), "output_directory": PATH, "allow_signature_invalidation": BOOLEAN}, ["output_directory"]),
    "transform_pages": ("Rotaciona, recorta, redimensiona ou altera margens. Recorte não remove conteúdo oculto.", {**SELECTION, **OUTPUT, "action": enum("rotate", "crop", "resize", "margins"), "angle": {"type": "integer", "enum": [0, 90, 180, 270, -90, -180, -270]}, "width": NUMBER, "height": NUMBER, "margins": RECT}, ["action", "output_path"]),
    "create": ("Cria PDF de imagens ou texto estruturado.", {**OUTPUT, "input_paths": array(PATH, 1, 2000), "text": {"type": "string", "maxLength": 2000000}, "title": string(), "width": NUMBER, "height": NUMBER}, ["output_path"]),
    "stamp": ("Aplica texto, imagem, PDF, marca-d'água ou numeração em uma saída nova.", {**OUTPUT, **SELECTION, "text": {"type": "string", "maxLength": 10000}, "image_path": PATH, "overlay_path": PATH, "font_size": NUMBER, "color": COLOR, "opacity": {"type": "number", "minimum": 0, "maximum": 1}, "position": enum("header", "footer", "page_number"), "action": enum("text", "image", "pdf", "watermark", "page_number")}, ["output_path"]),
    "metadata": ("Consulta ou substitui metadados.", {**OUTPUT, "action": enum("get", "set"), "values": FIELDS}, []),
    "bookmarks": ("Consulta ou substitui marcadores do documento.", {**OUTPUT, "action": enum("get", "set"), "entries": array(array({"type": ["integer", "string"]}, 3, 3))}, []),
    "attachments": ("Lista, extrai, adiciona ou remove anexos sem executá-los.", {**OUTPUT, "action": enum("list", "extract", "add", "remove"), "attachment_name": string(), "attachment_path": PATH, "output_directory": PATH}, []),
    "annotations": ("Lista, adiciona ou remove anotações.", {**OUTPUT, **SELECTION, "action": enum("list", "add", "remove"), "entries": array(object_schema({"page": POSITIVE, "rect": RECT, "type": enum("highlight", "text", "rectangle", "circle"), "text": {"type": "string"}, "color": COLOR}, ["page", "rect", "type"])), "xrefs": array(POSITIVE)}, []),
    "forms": ("Lista, exporta, preenche ou achata AcroForms; XFA não é editável.", {**OUTPUT, "action": enum("list", "export", "fill", "flatten"), "values": FIELDS}, []),
    "preview_redactions": ("Produz uma prévia; tarjas só se tornam definitivas em apply_redactions.", {**OUTPUT, **SELECTION, "redactions": array(REGION, 1), "query": string()}, ["output_path"]),
    "apply_redactions": ("Remove definitivamente conteúdo selecionado, regrava e verifica a saída.", {**OUTPUT, **SELECTION, "redactions": array(REGION, 1), "query": string()}, ["output_path"]),
    "sanitize": ("Remove metadados, anexos e ações ativas conforme os parâmetros.", {**OUTPUT, "remove_metadata": BOOLEAN, "remove_attachments": BOOLEAN, "remove_actions": BOOLEAN, "remove_hidden_text": BOOLEAN, "remove_links": BOOLEAN}, ["output_path"]),
    "optimize": ("Otimiza sem perdas por padrão; redução de imagens exige lossy=true.", {**OUTPUT, "lossy": BOOLEAN, "quality": {"type": "integer", "minimum": 1, "maximum": 100}, "image_dpi": {"type": "integer", "minimum": 36, "maximum": 600}}, ["output_path"]),
    "protect": ("Criptografa com AES-256 ou decifra usando senha conhecida resolvida no vault.", {**OUTPUT, "action": enum("encrypt", "decrypt"), "new_password_ref": PASSWORD, "permissions": array(enum("print", "copy", "annotate", "form"), 0, 4)}, ["action", "output_path"]),
    "repair": ("Regrava uma estrutura recuperável e verifica o documento resultante.", OUTPUT, ["output_path"]),
    "compare": ("Compara texto e aparência por página e informa diferenças.", {"other_path": PATH, "output_path": PATH, "output_directory": PATH, "dpi": {"type": "integer", "minimum": 36, "maximum": 150}}, ["other_path"]),
    "validate": ("Valida estrutura, assinaturas offline ou PDF/A; conversão PDF/A-2b exige veraPDF.", {**OUTPUT, "validation": enum("structure", "signatures", "pdfa"), "action": enum("check", "convert_pdfa"), "trust_roots": array(PATH, 0, 100)}, []),
    "make_searchable": ("Cria camada OCR pesquisável preservando o PDF de origem.", {**OUTPUT, **OCR_OPTIONS, "pages": SELECTION["pages"], "mode": enum("auto", "ocr", "native")}, ["output_path"]),
}
OCR_SPECS = {
    "recognize": ("Reconhece texto em imagem ou TIFF multipágina com coordenadas e confiança disponível.", {**OCR_OPTIONS, "output_path": PATH, "format": enum("text", "json", "markdown")}, []),
    "analyze_layout": ("Analisa layout e tabelas com PaddleOCR instalado; modelos locais são obrigatórios.", {**OCR_OPTIONS, "output_path": PATH}, []),
}
ALWAYS_WRITE = {"render_pages", "extract_images", "select_pages", "merge", "split", "transform_pages", "create", "stamp", "preview_redactions", "apply_redactions", "sanitize", "optimize", "protect", "repair", "make_searchable"}
JOB_SPECS = {"get_job": ("Consulta estado e progresso do job.", {"job_id": string()}, ["job_id"]),
             "get_job_result": ("Obtém resultado paginado. encoding=json-fragments exige concatenar result_fragment por next_offset e decodificar JSON.", {"job_id": string(), "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, ["job_id"]),
             "cancel_job": ("Cancela um job e remove somente suas saídas transitórias.", {"job_id": string()}, ["job_id"]),
             "list_jobs": ("Lista jobs do perfil selecionado.", {}, [])}


class DocumentToolRegistry:
    """Mantém jobs isolados por módulo e perfil; dependências são carregadas sob demanda."""

    def __init__(self, configuration, namespace):
        self.configuration, self.namespace = configuration, namespace
        self.jobs = Jobs(configuration.root, namespace)
        atexit.register(self.close)

    def close(self):
        self.jobs.close()

    def definitions(self):
        profiles = self.configuration.enabled_document_profiles(self.namespace)
        if not profiles:
            return []
        specs = {**(PDF_SPECS if self.namespace == "pdf" else OCR_SPECS), **JOB_SPECS,
                 "get_capabilities": ("Diagnostica motores, versões e capacidades instaladas sem baixar modelos.", {}, [])}
        if self.namespace == "ocr":
            specs["list_languages"] = ("Lista idiomas instalados para OCR local.", {}, [])
        result = []
        if len(profiles) > 1:
            result.append({"name": self.namespace + ".list_profiles", "description": "Lista perfis e contextos documentais habilitados.", "input_schema": object_schema({})})
        for operation, (description, fields, mandatory) in specs.items():
            properties, required = deepcopy(fields), list(mandatory)
            if operation not in JOB_SPECS and operation not in {"get_capabilities", "list_languages"}:
                if operation not in {"merge", "create"}:
                    properties.update(INPUT); required.append("input_path")
                properties["request_id"] = {**string("Identificador estável; repetir argumentos recupera o mesmo job."), "maxLength": 200}
                required.append("request_id")
                properties["confirm"] = {"type": "boolean", "const": True}
                if operation in ALWAYS_WRITE:
                    required.append("confirm")
                description += " Retorna job_id; consulte get_job/get_job_result."
            if len(profiles) > 1:
                properties["profile"] = enum(*(profile["name"] for profile in profiles)); required.append("profile")
            description += " Perfil: " + "; ".join(p["name"] + " — " + p["description"] for p in profiles)
            result.append({"name": self.namespace + "." + operation, "description": description, "input_schema": object_schema(properties, required)})
        return result

    def call(self, name, arguments):
        from jsonschema import Draft202012Validator
        definition = next((tool for tool in self.definitions() if tool["name"] == name), None)
        if definition is None:
            raise DocumentError("tool_unavailable", "Ferramenta documental indisponível.")
        if not isinstance(arguments, dict) or not Draft202012Validator(definition["input_schema"]).is_valid(arguments):
            raise DocumentError("invalid_argument", "Parâmetros documentais inválidos; consulte o contrato da ferramenta.")
        profiles = self.configuration.enabled_document_profiles(self.namespace)
        operation = name.split(".", 1)[1]
        if operation == "list_profiles":
            return {"profiles": [{"name": p["name"], "description": p["description"]} for p in profiles]}
        selected = profiles[0]["name"] if len(profiles) == 1 else arguments["profile"]
        document = load_configuration(self.configuration.root, self.namespace)
        profile = document["profiles"][selected]
        self.jobs.register(selected, profile, document["defaults"])
        if operation == "get_job":
            return self.jobs.get(selected, arguments["job_id"])
        if operation == "get_job_result":
            return self.jobs.result(selected, arguments["job_id"], arguments.get("offset", 0), arguments.get("limit", 100))
        if operation == "cancel_job":
            return self.jobs.cancel(selected, arguments["job_id"])
        if operation == "list_jobs":
            return self.jobs.list(selected)
        if operation in {"get_capabilities", "list_languages"}:
            from ..ocr.runtime import get_capabilities
            effective = profile
            if self.namespace == "pdf" and profile.get("ocr_profile"):
                ocr = load_configuration(self.configuration.root, "ocr")["profiles"].get(profile["ocr_profile"])
                if ocr and ocr["enabled"]:
                    effective = {**profile, **ocr}
            capabilities = get_capabilities(self.configuration.root, effective)
            return {"languages": capabilities.get("tesseract", {}).get("languages", [])} if operation == "list_languages" else capabilities
        writing = operation in ALWAYS_WRITE or any(key in arguments for key in ("output_path", "output_directory")) or arguments.get("action") in {"set", "fill", "flatten", "export", "add", "remove", "extract", "convert_pdfa"}
        if writing and arguments.get("confirm") is not True:
            raise DocumentError("confirmation_required", "A gravação exige confirm=true.")
        return self.jobs.submit(selected, profile, document["defaults"], operation, arguments, arguments["request_id"])
