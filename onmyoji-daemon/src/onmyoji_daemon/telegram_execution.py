"""Compatibilidade de importação para o núcleo multiorigem extraído."""
import sys
from .execution_gateway import execution

execution.TelegramExecution = execution.ExecutionGateway
sys.modules[__name__] = execution
