"""Agendador local independente do gateway do agente."""

# Contrato da RPC disponível sem carregar as dependências do motor cron.
SORT_FIELDS = ("id", "status", "action", "expression", "timezone", "next_run_at", "created_at", "channel", "thread_policy", "last_result")
