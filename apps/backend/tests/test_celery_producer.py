"""Тесты для Celery producer (backend → broker).

Используется мок Celery-инстанса, чтобы не требовать запущенный Redis.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.celery_producer import send_task


def test_send_task_validates_kind() -> None:
    with pytest.raises(ValueError, match="Unknown task kind"):
        send_task("nonsense", {})


def test_send_task_uses_correct_task_name() -> None:
    with patch("app.services.celery_producer._get_app") as mock_get_app:
        mock_app = MagicMock()
        mock_app.send_task.return_value = MagicMock(id="task-123")
        mock_get_app.return_value = mock_app

        result = send_task("penalty", {"x": 1})

        assert result == "task-123"
        mock_app.send_task.assert_called_once()
        args, kwargs = mock_app.send_task.call_args
        assert args[0] == "worker.tasks.process_penalty.run"
        assert kwargs == {"kwargs": {"payload": {"x": 1}}}


def test_send_task_for_checkin() -> None:
    with patch("app.services.celery_producer._get_app") as mock_get_app:
        mock_app = MagicMock()
        mock_app.send_task.return_value = MagicMock(id="task-456")
        mock_get_app.return_value = mock_app

        send_task("checkin", {"user_id": 42})

        args, kwargs = mock_app.send_task.call_args
        assert args[0] == "worker.tasks.process_checkin.run"


def test_send_task_for_payment() -> None:
    with patch("app.services.celery_producer._get_app") as mock_get_app:
        mock_app = MagicMock()
        mock_app.send_task.return_value = MagicMock(id="task-789")
        mock_get_app.return_value = mock_app

        send_task("payment", {"charge_id": "tg-1"})

        args, kwargs = mock_app.send_task.call_args
        assert args[0] == "worker.tasks.process_payment.run"


def test_send_task_lazy_app_creation() -> None:
    """_get_app() должен создать Celery-инстанс один раз."""
    import app.services.celery_producer as mod

    mod._app = None
    with patch("app.services.celery_producer.Celery") as mock_celery_cls:
        mock_instance = MagicMock()
        mock_celery_cls.return_value = mock_instance

        mod._get_app()
        mod._get_app()

        # Celery() вызван ровно один раз (lazy).
        assert mock_celery_cls.call_count == 1
        mod._app = None