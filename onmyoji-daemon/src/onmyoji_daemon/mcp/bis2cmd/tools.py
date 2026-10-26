"""Publicação das operações oficiais BIS2CMD."""
from __future__ import annotations
from typing import Any
from ..configuration import McpConfiguration
from .service import Bis2CmdService, COMMANDS, WRITE_OPERATIONS

DESCRIPTIONS = {
    "companies_list": "Lista empresas. args aceita pares limit/offset.",
    "certificates_list": "Lista certificados. args aceita companyId, limit e offset em pares.",
    "items_list": "Lista itens por id, codeId, status, code ou displayLine; aceita limit/offset.",
    "item_update": "Altera campo permitido de item ou código. args: itemId, codeId opcional, field, value e confirm.",
    "item_price_update": "Altera preço. args: companyId, codeId, price e confirm.",
    "doc_fiscal_repair": "Repara documento SEFAZPROBLEM. args: docId, blocos DOC/NFCE/ITEM/PAYMENT e confirm.",
    "fix_nfce": "Regenera XML de NFC-e para reenvio. args: docId <id> confirm.",
    "doc_fiscal_detail": "Consulta documento por id; key <44 dígitos>; ou serie <valor> number <valor>.",
    "nfce_list": "Lista NFC-e. args exige companyId; aceita status, validationStatus, validationErrorCode, start/end ISO-8601, limit/offset.",
    "validate_doc_fiscal": "Revalida documento fiscal. args: docId <id>.",
    "nfce_download_xml": "Consulta ou persiste XML na SEFAZ. args: companyId, certificateId, key; opcional output, dryRun ou confirm. output fica restrito às raízes de exportação.",
    "nfce_listagem_chaves": "Lista página de chaves na SEFAZ. args: companyId, certificateId, start ISO-8601 e end opcional; continue por metadata.next_start.",
    "nfce_inutilize_number": "Inutiliza faixas na SEFAZ. args: companyId, certificateId, conjuntos serie/numberStart/numberEnd, --workers 2-5 opcional e confirm.",
    "nfce_send_offline": "Envia NFC-e offline. args: docId <id>, ids <lista> ou all, seguido de confirm.",
    "export_nfe": "Exporta XMLs de NFe. args: startDate, endDate e outputPath; destino restrito às raízes de exportação.",
    "export_file": "Exporta arquivos DB. args: intervalo startDate/endDate, lastDays ou startID/endID, mais outputPath; destino restrito às raízes de exportação.",
    "import_file": "Restaura arquivos DB exportados. args: intervalo startDate/endDate ou startID/endID, mais inputPath; origem restrita às raízes de importação.",
}

class Bis2CmdToolRegistry:
    def __init__(self, configuration: McpConfiguration): self.configuration = configuration
    def definitions(self) -> list[dict[str, Any]]:
        profiles = self.configuration.enabled_bis2cmd_profiles()
        if not profiles: return []
        tools = []
        if len(profiles) > 1: tools.append({"name": "bis2cmd.list_profiles", "description": "Lista perfis BIS2CMD habilitados e seus contextos.", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}})
        for operation in sorted(COMMANDS):
            properties: dict[str, Any] = {"args": {"type": "array", "items": {"type": "string"}, "description": "Parâmetros oficiais na ordem esperada. Use [\"help\"] para obter a ajuda nativa do comando."}}; required = []
            if len(profiles) > 1: properties["profile"] = {"type": "string", "enum": [item["name"] for item in profiles]}; required.append("profile")
            if operation in WRITE_OPERATIONS: properties["confirm"] = {"type": "boolean", "const": True}; required.append("confirm")
            context = f' Perfil ativo: "{profiles[0]["name"]}" — {profiles[0]["description"]}' if len(profiles) == 1 else " Escolha profile conforme bis2cmd.list_profiles."
            schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
            if required: schema["required"] = required
            tools.append({"name": "bis2cmd." + operation, "description": DESCRIPTIONS[operation] + (" Exige confirm=true." if operation in WRITE_OPERATIONS else "") + context, "input_schema": schema})
        return tools
    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        profiles = self.configuration.enabled_bis2cmd_profiles()
        if name == "bis2cmd.list_profiles" and len(profiles) > 1: return {"profiles": profiles}
        selected = profiles[0]["name"] if len(profiles) == 1 else arguments.get("profile")
        if not isinstance(selected, str) or selected not in {item["name"] for item in profiles}: raise ValueError("Perfil BIS2CMD indisponível.")
        operation = name.removeprefix("bis2cmd.") if name.startswith("bis2cmd.") else ""
        if operation not in COMMANDS: raise ValueError("Tool BIS2CMD indisponível.")
        return Bis2CmdService(self.configuration.root).invoke(selected, operation, arguments)
