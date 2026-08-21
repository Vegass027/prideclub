"""Unit-тесты CharacterService (Task 3.3) — in-memory stubs.

Используем in-memory stubs локально в файле (НЕ в tests/fakes.py).
Mock-сессия для SQL не используется — тестируем поведение через
real mutations на in-memory ORM-объектах.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.core.exceptions import StatDefinitionMissingError
from app.models.stat_definition import StatDefinition
from app.models.user_status import UserStatus
from app.models.user_stats import UserStats
from app.services.character_service import CharacterService


# ─── In-memory stubs (local, не в tests/fakes.py) ──────────

class _InMemoryUserStatsRepo:
    def __init__(self) -> None:
        self._stats: dict[tuple[int, str], UserStats] = {}
        self.get_or_create_calls: list[tuple[int, str]] = []
        self.try_get_calls: list[tuple[int, str]] = []
        self.unfreeze_calls: list[UserStats] = []
        self.increment_calls: list[tuple[UserStats, int]] = []
        self.decrement_calls: list[tuple[UserStats, int]] = []
        self.freeze_calls: list[tuple[UserStats, str]] = []
        self.touch_calls: list[UserStats] = []

    def add(self, stat: UserStats) -> None:
        self._stats[(stat.user_id, stat.stat_definition_id)] = stat

    async def get_or_create_for_update(
        self, *, user_id: int, stat_definition_id: str,
    ) -> tuple[UserStats, bool]:
        self.get_or_create_calls.append((user_id, stat_definition_id))
        key = (user_id, stat_definition_id)
        if key in self._stats:
            return self._stats[key], False
        new = UserStats(
            id=str(uuid4()),
            user_id=user_id,
            stat_definition_id=stat_definition_id,
            value=0,
        )
        self._stats[key] = new
        return new, True

    async def try_get_for_update(
        self, *, user_id: int, stat_definition_id: str,
    ) -> UserStats | None:
        self.try_get_calls.append((user_id, stat_definition_id))
        return self._stats.get((user_id, stat_definition_id))

    async def list_for_user(self, user_id: int) -> list[UserStats]:
        items = [s for s in self._stats.values() if s.user_id == user_id]
        return sorted(items, key=lambda s: s.value, reverse=True)

    async def unfreeze(self, stat: UserStats) -> UserStats:
        self.unfreeze_calls.append(stat)
        stat.is_frozen = False
        stat.frozen_at = None
        stat.frozen_reason_text = None
        stat.last_checkin_at = datetime.now(tz=timezone.utc)
        return stat

    async def increment_value(self, stat: UserStats, gain: int) -> UserStats:
        self.increment_calls.append((stat, gain))
        if gain <= 0:
            return stat
        stat.value = stat.value + gain
        return stat

    async def decrement_with_floor(
        self, stat: UserStats, loss: int,
    ) -> UserStats:
        self.decrement_calls.append((stat, loss))
        if loss <= 0:
            return stat
        stat.value = max(0, stat.value - loss)
        return stat

    async def freeze(self, stat: UserStats, reason_text: str) -> UserStats:
        self.freeze_calls.append((stat, reason_text))
        stat.is_frozen = True
        stat.frozen_at = datetime.now(tz=timezone.utc)
        stat.frozen_reason_text = reason_text
        return stat

    async def touch_last_checkin(self, stat: UserStats) -> None:
        self.touch_calls.append(stat)
        stat.last_checkin_at = datetime.now(tz=timezone.utc)


class _InMemoryUserStatusRepo:
    def __init__(self) -> None:
        self._statuses: list[UserStatus] = []

    def add(self, s: UserStatus) -> None:
        self._statuses.append(s)

    async def list_all_ordered(self) -> list[UserStatus]:
        return sorted(self._statuses, key=lambda s: s.sort_order)


class _InMemoryStatDefinitionRepo:
    def __init__(self) -> None:
        self._defs: dict[str, StatDefinition] = {}

    def add(self, d: StatDefinition) -> None:
        self._defs[str(d.id)] = d

    async def get_by_id(self, id: str) -> StatDefinition | None:
        return self._defs.get(id)


def _seed_statuses(status_repo: _InMemoryUserStatusRepo) -> None:
    """Засеять все 5 ступеней для тестов get_character."""
    for i, (name, threshold, icon) in enumerate([
        ("На старте", 0, "🐣"),
        ("В потоке", 30, "🌊"),
        ("На волне", 100, "⚡"),
        ("В форме", 300, "🔥"),
        ("Режим зверя", 700, "🐺"),
    ]):
        status_repo.add(UserStatus(
            id=f"us-{i+1}",
            status_name=name,
            min_threshold=threshold,
            icon=icon,
            sort_order=i + 1,
        ))


def _make_three_repo_service(
    *,
    with_stats: list[UserStats] | None = None,
    seed_statuses: bool = False,
) -> tuple[
    CharacterService, _InMemoryUserStatsRepo, _InMemoryStatDefinitionRepo,
]:
    """Service с пустыми статусами по умолчанию (для тестов, которым
    не нужен status catalog). get_character-тесты собирают service
    вручную через _InMemory*Repo."""
    stats_repo = _InMemoryUserStatsRepo()
    sd_repo = _InMemoryStatDefinitionRepo()
    status_repo = _InMemoryUserStatusRepo()
    if with_stats:
        for s in with_stats:
            stats_repo.add(s)
    if seed_statuses:
        _seed_statuses(status_repo)
    service = CharacterService(
        user_stats_repo=stats_repo,
        user_status_repo=status_repo,
        stat_definition_repo=sd_repo,
    )
    return service, stats_repo, sd_repo


# ─── 1. increment_on_checkin ─────────────────────────────────

@pytest.mark.asyncio
async def test_increment_on_checkin_first_time_creates_row_with_gain() -> None:
    """Первый чек-ин: get_or_create создал row с value=0, потом +=gain."""
    service, stats_repo, _ = _make_three_repo_service(seed_statuses=False)

    new_value = await service.increment_on_checkin(
        user_id=42, stat_definition_id="intel", gain=2
    )

    assert new_value == 2
    assert stats_repo.get_or_create_calls == [(42, "intel")]
    assert stats_repo.unfreeze_calls == []
    assert len(stats_repo.increment_calls) == 1
    assert stats_repo.increment_calls[0][1] == 2
    assert len(stats_repo.touch_calls) == 1
    saved = stats_repo._stats[(42, "intel")]
    assert saved.value == 2


@pytest.mark.asyncio
async def test_increment_on_checkin_unfreezes_when_frozen() -> None:
    """frozen=True → unfreeze → +=gain. Все 4 frozen-поля очищены,
    last_checkin_at обновлён внутри unfreeze."""
    old_frozen_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    frozen = UserStats(
        id="s-frozen", user_id=42, stat_definition_id="intel",
        value=5, is_frozen=True, frozen_at=old_frozen_at,
        frozen_reason_text="...",
        last_checkin_at=old_frozen_at,
    )
    service, stats_repo, _ = _make_three_repo_service(
        with_stats=[frozen], seed_statuses=False,
    )

    before = datetime.now(tz=timezone.utc)
    new_value = await service.increment_on_checkin(
        user_id=42, stat_definition_id="intel", gain=3
    )
    after = datetime.now(tz=timezone.utc)

    assert new_value == 8
    assert len(stats_repo.unfreeze_calls) == 1
    assert frozen.is_frozen is False
    assert frozen.frozen_at is None
    assert frozen.frozen_reason_text is None
    assert frozen.value == 8
    # ⚠️ last_checkin_at обновлён внутри [before, after]
    # (repo.unfreeze вызывает touch внутренне).
    assert before <= frozen.last_checkin_at <= after


@pytest.mark.asyncio
async def test_increment_on_checkin_touches_last_checkin_always() -> None:
    """Даже при gain=0 (WARN no-op) last_checkin_at обновляется."""
    old_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
    stat = UserStats(
        id="s", user_id=42, stat_definition_id="intel",
        value=10, last_checkin_at=old_time,
    )
    service, _, _ = _make_three_repo_service(
        with_stats=[stat], seed_statuses=False,
    )

    before = datetime.now(tz=timezone.utc)
    await service.increment_on_checkin(
        user_id=42, stat_definition_id="intel", gain=0
    )
    after = datetime.now(tz=timezone.utc)

    assert stat.value == 10
    assert before <= stat.last_checkin_at <= after


# ─── 2. decrement_on_penalty ───────────────────────────────

@pytest.mark.asyncio
async def test_decrement_on_penalty_no_existing_row_returns_none_without_create() -> None:
    """⚠️ КРИТИЧНЫЙ INVARIANT: stat нет → None, NO get_or_create_for_update.

    Главный invariant по правилу Дмитрия 21.08.2026: пойманный юзер,
    который ни разу не чек-инился в stat, НЕ должен порождать
    пустую stat-строку. Иначе профиль получает «характеристики
    только из штрафов».
    """
    service, stats_repo, _ = _make_three_repo_service(seed_statuses=False)

    result = await service.decrement_on_penalty(
        user_id=42, stat_definition_id="intel", loss=5
    )

    assert result is None
    assert stats_repo.try_get_calls == [(42, "intel")]
    assert stats_repo.get_or_create_calls == [], (
        "decrement_on_penalty НЕ должен вызывать "
        "get_or_create_for_update — иначе создастся пустая stat"
    )
    assert (42, "intel") not in stats_repo._stats
    assert stats_repo.decrement_calls == []


@pytest.mark.asyncio
async def test_decrement_on_penalty_existing_row_normal_decrement() -> None:
    stat = UserStats(
        id="s", user_id=42, stat_definition_id="intel", value=10,
    )
    service, stats_repo, _ = _make_three_repo_service(
        with_stats=[stat], seed_statuses=False,
    )

    result = await service.decrement_on_penalty(
        user_id=42, stat_definition_id="intel", loss=3
    )

    assert result == 7
    assert stat.value == 7
    assert len(stats_repo.decrement_calls) == 1
    assert stats_repo.decrement_calls[0][1] == 3
    assert stats_repo.try_get_calls == [(42, "intel")]
    assert stats_repo.get_or_create_calls == []


@pytest.mark.asyncio
async def test_decrement_on_penalty_floors_at_zero() -> None:
    """value=1, loss=5 → value=0 (floor, не -4)."""
    stat = UserStats(
        id="s", user_id=42, stat_definition_id="intel", value=1,
    )
    service, _, _ = _make_three_repo_service(
        with_stats=[stat], seed_statuses=False,
    )

    result = await service.decrement_on_penalty(
        user_id=42, stat_definition_id="intel", loss=5
    )

    assert result == 0
    assert stat.value == 0


@pytest.mark.asyncio
async def test_decrement_on_penalty_works_on_frozen_stat() -> None:
    """Frozen stat — всё равно декрементим (catch не размораживает).

    Разморозка наступает при следующем ЧЕК-ИНЕ (increment_on_checkin),
    не на catch. Это сознательная семантика: пойманный юзер видит
    последствия 100%.
    """
    old_frozen = datetime(2026, 1, 1, tzinfo=timezone.utc)
    stat = UserStats(
        id="s", user_id=42, stat_definition_id="intel", value=10,
        is_frozen=True, frozen_at=old_frozen,
        frozen_reason_text="...",
    )
    service, stats_repo, _ = _make_three_repo_service(
        with_stats=[stat], seed_statuses=False,
    )

    await service.decrement_on_penalty(
        user_id=42, stat_definition_id="intel", loss=3
    )

    assert stat.value == 7
    assert stat.is_frozen is True  # всё ещё frozen
    assert stat.frozen_at == old_frozen
    # 🛑 УНFREEZE НЕ звали — это другой поток (catch-only).
    assert stats_repo.unfreeze_calls == []


# ─── 3. apply_freeze ────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_freeze_idempotent_skips_already_frozen() -> None:
    """⚠️ Idempotency invariant: frozen=True → без модификации.

    Главный invariant (per Dmitry 21.08.2026 Variant 1):
    повторный вызов apply_freeze с другим reason НЕ перезаписывает
    frozen_at / frozen_reason_text. История сохраняется.

    Если кто-то удалит guard `if stat.is_frozen: return stat` —
    тест УПАДЁТ (потому что repo.freeze будет вызван).
    """
    original_frozen_at = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    original_reason = "Первая заморозка"
    stat = UserStats(
        id="s", user_id=42, stat_definition_id="intel", value=5,
        is_frozen=True,
        frozen_at=original_frozen_at,
        frozen_reason_text=original_reason,
    )
    service, stats_repo, _ = _make_three_repo_service(
        with_stats=[stat], seed_statuses=False,
    )

    result = await service.apply_freeze(
        user_id=42, stat_definition_id="intel",
        reason="ВТОРАЯ заморозка с ДРУГИМ reason",
    )

    assert result is stat
    # ⚠️ repo.freeze() НЕ звали.
    assert stats_repo.freeze_calls == []
    # ⚠️ frozen_at НЕ изменился.
    assert stat.frozen_at == original_frozen_at
    # ⚠️ frozen_reason_text НЕ изменился.
    assert stat.frozen_reason_text == original_reason


@pytest.mark.asyncio
async def test_apply_freeze_unfrozen_calls_repo_freeze_with_reason() -> None:
    stat = UserStats(
        id="s", user_id=42, stat_definition_id="intel", value=10,
        is_frozen=False,
    )
    service, stats_repo, _ = _make_three_repo_service(
        with_stats=[stat], seed_statuses=False,
    )

    reason = "Характеристика заморожена: ..."
    result = await service.apply_freeze(
        user_id=42, stat_definition_id="intel", reason=reason,
    )

    assert result is stat
    assert len(stats_repo.freeze_calls) == 1
    assert stats_repo.freeze_calls[0] == (stat, reason)
    # State:
    assert stat.is_frozen is True
    assert stat.frozen_reason_text == reason


@pytest.mark.asyncio
async def test_apply_freeze_no_existing_stat_returns_none() -> None:
    """Stat отсутствует → return None, ничего не создаём."""
    service, stats_repo, _ = _make_three_repo_service(seed_statuses=False)

    result = await service.apply_freeze(
        user_id=42, stat_definition_id="intel", reason="...",
    )

    assert result is None
    assert stats_repo.freeze_calls == []
    # ⚠️ get_or_create_for_update НЕ звали.
    assert stats_repo.get_or_create_calls == []
    # Только try_get_for_update.
    assert stats_repo.try_get_calls == [(42, "intel")]


# ─── 4. get_character ───────────────────────────────────────

@pytest.mark.asyncio
async def test_get_character_filters_zeros_unless_frozen() -> None:
    """Stats: [value=0 not frozen, value=0 frozen, value=5, value=10].

    В stats[] только три последних — zero-frozen не отфильтрован
    (UI рисует ❄ + frozen_reason_text). Zero not-frozen исключён.
    Фильтр через `>= MIN_STAT_VALUE_TO_SHOW (= 1) OR is_frozen`.
    """
    zero_notfrozen_sd = StatDefinition(
        id="sd-zn", slug="zn", name="ZeroNotFrozen", icon="⚡",
        sort_order=1, is_active=True,
    )
    zero_frozen_sd = StatDefinition(
        id="sd-zf", slug="zf", name="ZeroFrozen", icon="🔥",
        sort_order=2, is_active=True,
    )
    five_sd = StatDefinition(
        id="sd-5", slug="five", name="Five", icon="⚡",
        sort_order=3, is_active=True,
    )
    ten_sd = StatDefinition(
        id="sd-10", slug="ten", name="Ten", icon="🔥",
        sort_order=4, is_active=True,
    )

    stats_repo = _InMemoryUserStatsRepo()
    stats_repo.add(UserStats(id="u-zn", user_id=42, stat_definition_id="sd-zn", value=0))
    stats_repo.add(UserStats(
        id="u-zf", user_id=42, stat_definition_id="sd-zf", value=0,
        is_frozen=True,
        frozen_reason_text="Характеристика заморожена: ...",
    ))
    stats_repo.add(UserStats(id="u-5", user_id=42, stat_definition_id="sd-5", value=5))
    stats_repo.add(UserStats(id="u-10", user_id=42, stat_definition_id="sd-10", value=10))
    sd_repo = _InMemoryStatDefinitionRepo()
    sd_repo.add(zero_notfrozen_sd)
    sd_repo.add(zero_frozen_sd)
    sd_repo.add(five_sd)
    sd_repo.add(ten_sd)
    status_repo = _InMemoryUserStatusRepo()
    _seed_statuses(status_repo)

    service = CharacterService(
        user_stats_repo=stats_repo,
        user_status_repo=status_repo,
        stat_definition_repo=sd_repo,
    )

    payload = await service.get_character(user_id=42)

    assert payload["total_value"] == 15  # 0 + 0 + 5 + 10
    visible_ids = [s["stat_definition_id"] for s in payload["stats"]]
    assert visible_ids == ["sd-10", "sd-5", "sd-zf"]
    assert "sd-zn" not in visible_ids
    zf = next(s for s in payload["stats"] if s["stat_definition_id"] == "sd-zf")
    assert zf["is_frozen"] is True
    assert zf["value"] == 0
    assert zf["frozen_reason_text"] == "Характеристика заморожена: ..."
    # ⚠️ last_checkin_at — datetime | None (НЕ строка, per Dmitry).
    assert isinstance(zf["last_checkin_at"], (type(None), datetime))


@pytest.mark.asyncio
async def test_get_character_calculates_status_from_total() -> None:
    """total=150 → «На волне» (100), next=«В форме» (300)."""
    sd = StatDefinition(
        id="sd-1", slug="intel", name="Интеллект", icon="🧠",
        sort_order=1, is_active=True,
    )
    stat = UserStats(
        id="u-1", user_id=42, stat_definition_id="sd-1", value=150,
    )

    stats_repo = _InMemoryUserStatsRepo()
    stats_repo.add(stat)
    sd_repo = _InMemoryStatDefinitionRepo()
    sd_repo.add(sd)
    status_repo = _InMemoryUserStatusRepo()
    _seed_statuses(status_repo)

    service = CharacterService(
        user_stats_repo=stats_repo,
        user_status_repo=status_repo,
        stat_definition_repo=sd_repo,
    )

    payload = await service.get_character(user_id=42)

    assert payload["total_value"] == 150
    assert payload["status"]["name"] == "На волне"
    assert payload["status"]["icon"] == "⚡"
    assert payload["status"]["next_threshold"] == 300
    assert payload["status"]["next_status"] == "В форме"
    # ⚠️ last_checkin_at — datetime | None в сервисе, сериализация
    # НЕ здесь (Task 3.6 API-схема).
    assert isinstance(
        payload["stats"][0]["last_checkin_at"], (type(None), datetime)
    )


@pytest.mark.asyncio
async def test_get_character_empty_stats_returns_start_status() -> None:
    """У юзера нет ни одной stat-строки.

    Контракт: total_value=0, status="На старте" (threshold=0,
    всегда существует), stats=[].
    """
    stats_repo = _InMemoryUserStatsRepo()
    sd_repo = _InMemoryStatDefinitionRepo()
    status_repo = _InMemoryUserStatusRepo()
    _seed_statuses(status_repo)

    service = CharacterService(
        user_stats_repo=stats_repo,
        user_status_repo=status_repo,
        stat_definition_repo=sd_repo,
    )

    payload = await service.get_character(user_id=42)

    assert payload == {
        "total_value": 0,
        "status": {
            "name": "На старте",
            "icon": "🐣",
            "next_threshold": 30,
            "next_status": "В потоке",
        },
        "stats": [],
    }


@pytest.mark.asyncio
async def test_get_character_raises_when_stat_definition_is_missing() -> None:
    """⚠️ Referential integrity баг → StatDefinitionMissingError.

    НЕ МАСКИРУЕМ пустыми строками (per Dmitry 21.08.2026).
    Исключение с extras (stat_definition_id, user_stats_id, user_id)
    делает проблему наблюдаемой для вызывающего API-слоя.

    ⚠️ Маппинг DomainError → HTTP покрывается в Task 3.6,
    не в Task 3.3.
    """
    sd_present = StatDefinition(
        id="sd-present", slug="present", name="Present", icon="⚡",
        sort_order=1, is_active=True,
    )
    stat_present = UserStats(
        id="u-present", user_id=42, stat_definition_id="sd-present",
        value=5,
    )
    stat_missing = UserStats(
        id="u-missing", user_id=42, stat_definition_id="sd-missing",
        value=5,
    )

    stats_repo = _InMemoryUserStatsRepo()
    stats_repo.add(stat_present)
    stats_repo.add(stat_missing)
    sd_repo = _InMemoryStatDefinitionRepo()
    sd_repo.add(sd_present)
    status_repo = _InMemoryUserStatusRepo()
    _seed_statuses(status_repo)

    service = CharacterService(
        user_stats_repo=stats_repo,
        user_status_repo=status_repo,
        stat_definition_repo=sd_repo,
    )

    with pytest.raises(StatDefinitionMissingError) as exc_info:
        await service.get_character(user_id=42)

    err = exc_info.value
    # Атрибуты из DomainError.__init__, проверены выше:
    assert err.status_code == 500
    assert err.code == "stat_definition_missing"
    assert err.extras["stat_definition_id"] == "sd-missing"
    assert err.extras["user_stats_id"] == "u-missing"
    assert err.extras["user_id"] == 42
    # message = f-string из __init__, содержит оба id.
    assert "sd-missing" in err.message
    assert "u-missing" in err.message


@pytest.mark.asyncio
async def test_get_character_empty_status_catalog_uses_start_fallback() -> None:
    """Defensive fallback: пустой user_statuses → «На старте».

    На проде миграция 019 засеивает 5 ступеней; пустой справочник
    — теоретический bug (ручной DELETE / будущий data-pipeline).
    Service НЕ падает, выдаёт «На старте».

    ⚠️ Per Dmitry 21.08.2026 — fallback оставлен явно покрытым
    этим тестом, чтобы случайное удаление было замечено.
    """
    sd = StatDefinition(
        id="sd-1", slug="intel", name="Интеллект", icon="🧠",
        sort_order=1, is_active=True,
    )
    stat = UserStats(
        id="u-1", user_id=42, stat_definition_id="sd-1", value=15,
    )

    stats_repo = _InMemoryUserStatsRepo()
    stats_repo.add(stat)
    sd_repo = _InMemoryStatDefinitionRepo()
    sd_repo.add(sd)
    status_repo = _InMemoryUserStatusRepo()  # ⚠️ пустой

    service = CharacterService(
        user_stats_repo=stats_repo,
        user_status_repo=status_repo,
        stat_definition_repo=sd_repo,
    )

    payload = await service.get_character(user_id=42)

    assert payload["total_value"] == 15
    assert payload["status"] == {
        "name": "На старте",
        "icon": "🐣",
        "next_threshold": None,
        "next_status": None,
    }
    assert len(payload["stats"]) == 1
    assert payload["stats"][0]["stat_definition_id"] == "sd-1"
