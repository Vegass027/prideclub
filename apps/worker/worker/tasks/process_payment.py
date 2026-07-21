from __future__ import annotations

from app.core.logging import get_logger
from app.services.payment_service import PaymentService
from db.session import async_session_factory  # type: ignore[import-not-found]


async def _process(payload: dict, *, session_factory=None) -> dict:
    """Идемпотентное подтверждение платежа через Telegram Payments.

    Идемпотентность обеспечивается UNIQUE-индексом
    `transactions.idempotency_key` (== charge_id). Дубль charge_id → идемпотентный
    no-op с возвратом существующей транзакции.

    DI: session_factory (опциональный) — позволяет тестам инжектить свой
    in-memory engine, не патча глобальный модуль.
    """
    log = get_logger("worker.process_payment")
    factory = session_factory if session_factory is not None else async_session_factory
    async with factory() as session:
        try:
            service = PaymentService(session)
            if payload["kind"] == "subscription":
                tx = await service.confirm_subscription(
                    charge_id=payload["charge_id"],
                    user_id=payload["user_id"],
                    habit_id=payload["habit_id"],
                    amount_kopecks=payload["amount_kopecks"],
                    months=payload.get("months", 1),
                )
            elif payload["kind"] == "deposit_topup":
                tx = await service.confirm_deposit_topup(
                    charge_id=payload["charge_id"],
                    user_id=payload["user_id"],
                    habit_id=payload["habit_id"],
                    amount_kopecks=payload["amount_kopecks"],
                )
            else:
                return {"ok": False, "code": "unknown_kind"}
            await session.commit()
            log.info(
                "worker_payment_ok",
                extra={
                    "transaction_id": str(tx.id),
                    "charge_id": payload["charge_id"],
                    "kind": payload["kind"],
                    "amount_kopecks": payload["amount_kopecks"],
                },
            )
            return {"ok": True, "transaction_id": str(tx.id)}
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            log.error("worker_payment_failed", extra={"err": str(exc)})
            return {"ok": False, "err": str(exc)}


try:
    from worker.celery_app import celery_app
except ImportError:
    celery_app = None  # type: ignore


if celery_app is not None:

    @celery_app.task(
        name="worker.tasks.process_payment.run",
        bind=True,
        max_retries=3,
        autoretry_for=(Exception,),
        retry_backoff=True,
        retry_backoff_max=120,
        retry_jitter=True,
    )
    def run(self, payload: dict) -> dict:  # type: ignore[no-redef]
        import asyncio

        return asyncio.run(_process(payload, session_factory=async_session_factory))
else:
    run = _process