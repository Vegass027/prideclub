"""Reusable e2e-simulation helpers for full user journey.

Импортируется из scenario_*.py. Никакой бизнес-логики тут нет — только
генерация валидных credentials (initData, service JWT) и описание
синтетического Telegram-юзера.

Безопасность: секреты (BOT_TOKEN, SERVICE_SECRET) сюда НЕ пробрасываются
и нигде не логируются. Сценарий передаёт их непосредственно в функции
подписи, как keyword-only аргументы.
"""
