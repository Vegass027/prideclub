from __future__ import annotations

import pytest

from app.core.exceptions import InvalidPrizeRulesError
from app.services.season_service import validate_prize_rules


def test_validate_prize_rules_ok() -> None:
    rules = [
        {"metric": "streak", "rank_from": 1, "rank_to": 1, "percentage": 40.0},
        {"metric": "streak", "rank_from": 2, "rank_to": 2, "percentage": 20.0},
        {"metric": "streak", "rank_from": 3, "rank_to": 3, "percentage": 10.0},
        {"metric": "catches", "rank_from": 1, "rank_to": 1, "percentage": 20.0},
        {"metric": "catches", "rank_from": 2, "rank_to": 2, "percentage": 10.0},
    ]
    validate_prize_rules(rules)  # не должно бросить


def test_validate_prize_rules_must_sum_100_per_metric() -> None:
    rules = [
        {"metric": "streak", "rank_from": 1, "rank_to": 1, "percentage": 30.0},
        {"metric": "streak", "rank_from": 2, "rank_to": 2, "percentage": 20.0},
        # 50% по streak — ошибка
    ]
    with pytest.raises(InvalidPrizeRulesError):
        validate_prize_rules(rules)


def test_validate_prize_rules_invalid_range() -> None:
    rules = [
        {"metric": "streak", "rank_from": 5, "rank_to": 2, "percentage": 100.0},
    ]
    with pytest.raises(InvalidPrizeRulesError):
        validate_prize_rules(rules)