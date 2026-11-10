"""Processo adaptador do broker OAuth para Mercado Pago."""
from .oauth_adapter import main_for

if __name__ == "__main__":
    raise SystemExit(main_for("mercadopago"))
