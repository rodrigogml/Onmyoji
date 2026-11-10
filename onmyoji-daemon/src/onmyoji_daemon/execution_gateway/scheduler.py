"""Seleção global antes da reserva, sem bloquear em destinos ocupados."""
from __future__ import annotations

import threading

from ..telegram_persistence import GatewayPersistenceError


def wake(gateway, conversation: str) -> None:
    with gateway.workers_changed:
        if not gateway.stop_event.is_set() and "__global__" not in gateway.workers:
            gateway.workers.add("__global__")
            threading.Thread(target=dispatch, args=(gateway,), daemon=True, name="execution-gateway-scheduler").start()
        gateway.workers_changed.notify_all()


def dispatch(gateway) -> None:
    try:
        while not gateway.stop_event.is_set():
            with gateway.app_server_lock:
                for destination, slot in list(gateway._slots.items()):
                    if slot.done.is_set():
                        del gateway._slots[destination]
                active = dict(gateway._slots)
            for destination, slot in active.items():
                item = gateway.store.next_input(destination, gateway._generation(destination))
                if item and item.get("origin", "telegram") == "telegram" and slot.active and getattr(slot.active, "channel_type", "telegram") == "telegram" and gateway._can_steer(slot, item):
                    gateway._steer_input(slot, item)
            admitted = False
            slot = None
            if gateway.work.acquire(blocking=False):
                try:
                    # A prioridade é decidida depois da capacidade, sob o mutex
                    # do enqueue, nunca a partir de um snapshot anterior à espera.
                    with gateway.database_lock:
                        for destination in gateway.store.candidates():
                            if destination in active or gateway.store.blocked(destination):
                                continue
                            generation = gateway._generation(destination)
                            item = gateway.store.next_input(destination, generation)
                            if not item:
                                continue
                            from ..telegram import ConversationExecution
                            identifier = gateway.store.reserve(destination, generation, item["id"], gateway.settings.turn_timeout)
                            slot = ConversationExecution(identifier, destination, generation, item)
                            slot.budget_reserved = True
                            break
                    if slot:
                        with gateway.app_server_lock:
                            gateway._slots[destination] = slot
                        active[destination] = slot
                        threading.Thread(target=gateway._execute_inbox, args=(slot,), daemon=True).start()
                        admitted = True
                except GatewayPersistenceError:
                    pass
                finally:
                    if not admitted:
                        gateway.work.release()
            with gateway.workers_changed:
                if not active and not gateway.store.candidates():
                    return
                if not admitted:
                    gateway.workers_changed.wait(timeout=0.1)
    finally:
        with gateway.workers_changed:
            gateway.workers.discard("__global__")
            gateway.workers_changed.notify_all()
            if not gateway.stop_event.is_set() and gateway.store.candidates():
                gateway.workers.add("__global__")
                threading.Thread(target=dispatch, args=(gateway,), daemon=True).start()
