"""Тесты для GET /api/v1/character/me (Phase 3 v2 Task 3.6).

Использует FastAPI TestClient + dependency_overrides для подмены
get_character_service на fake. CharacterService сам конструируется
поверх in-memory stubs (те же что в tests/test_character_service.py).
"""
from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta
from uuid import uuid4

os.environ.setdefault("STATIC_DIR", tempfile.mkdtemp(prefix="hc_character_static_"))
os.environ.setdefault("BOT_TOKEN", "test-bot-token")
os.environ.setdefault("SERVICE_SECRET", "test")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "https://web.telegram.org")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SSE_TOKEN_SECRET", "test-sse-token-secret")


def _build_init_data(*, user_id: int) -> str:
    import hashlib, hmac, time, urllib.parse
    user = {"id": user_id, "first_name": "Alice", "username": "alice"}
    params = {
        "user": __import__("json").dumps(user, separators=(",", ":")),
        "auth_date": str(int(time.time())),
        "query_id": "q",
    }
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", b"test-bot-token", hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(params)


HEADERS = {"X-Telegram-Init-Data": _build_init_data(user_id=999)}

# ── In-memory stubs ──────────────────────────────────────────────


class _InMemoryUserStatsRepo:
    def __init__(self) -> None:
        self._stats: dict[tuple[int, str], dict] = {}

    def add(
        self,
        user_id: int,
        stat_definition_id: str,
        *,
        value: int,
        is_frozen: bool,
        last_checkin_at: datetime | None = None,
    ) -> None:
        # id не используется CharacterService для логики, но StatDefinitionMissingError
        # требует user_stats_id. Генерим unique uuid4.
        self._stats[(user_id, stat_definition_id)] = {
            "id": str(uuid4()),
            "user_id": user_id,
            "stat_definition_id": stat_definition_id,
            "value": value,
            "is_frozen": is_frozen,
            "frozen_reason_text": (
                "Характеристика заморожена: ..." if is_frozen else None
            ),
            "last_checkin_at": last_checkin_at,
        }

    async def list_for_user(self, user_id: int) -> list[_StatOut]:
        # CharacterService.get_character читает .value/.is_frozen/.frozen_reason_text
        # /.last_checkin_at/.stat_definition_id. Возвращаем объекты с этими атрибутами.
        items = [
            _StatOut(**v) for v in self._stats.values() if v["user_id"] == user_id
        ]
        return sorted(items, key=lambda s: s.value, reverse=True)

    async def get_or_create_for_update(self, *, user_id, stat_definition_id, gain):
        key = (user_id, stat_definition_id)
        s = self._stats.get(key)
        if s is None:
            self.add(user_id, stat_definition_id, value=0, is_frozen=False)
            s = self._stats[key]
        return s, True

    async def decrement_with_floor(self, s, loss):
        s["value"] = max(0, s["value"] - loss)
        return s

    async def unfreeze(self, s):
        s["is_frozen"] = False
        s["frozen_at"] = None
        s["frozen_reason_text"] = None
        s["last_checkin_at"] = datetime.now(tz=UTC)
        return s

    async def increment_value(self, s, gain):
        if gain <= 0:
            return s
        s["value"] = s["value"] + gain
        return s

    async def touch_last_checkin(self, s):
        s["last_checkin_at"] = datetime.now(tz=UTC)

    async def try_get_for_update(self, *, user_id, stat_definition_id):
        return self._stats.get((user_id, stat_definition_id))


class _StubStatus:
    """Minimal UserStatus-like obj (без SQLAlchemy-dep)."""

    def __init__(
        self, status_name: str, min_threshold: int, icon: str, sort_order: int,
    ) -> None:
        self.status_name = status_name
        self.min_threshold = min_threshold
        self.icon = icon
        self.sort_order = sort_order


class _InMemoryUserStatusRepo:
    def __init__(self) -> None:
        self._statuses: list = []

    def add(self, name: str, threshold: int, icon: str, sort_order: int) -> None:
        self._statuses.append(
            _StubStatus(name, threshold, icon, sort_order)
        )

    async def list_all_ordered(self):
        return sorted(self._statuses, key=lambda s: s.sort_order)




class _StatOut:
    """Минимальный объект с полями, которые читает CharacterService.get_character."""

    __slots__ = (
        "id", "user_id", "stat_definition_id", "value", "is_frozen",
        "frozen_reason_text", "last_checkin_at",
    )

    def __init__(
        self, *, id, user_id, stat_definition_id, value, is_frozen,
        frozen_reason_text, last_checkin_at,
    ) -> None:
        self.id = id
        self.user_id = user_id
        self.stat_definition_id = stat_definition_id
        self.value = value
        self.is_frozen = is_frozen
        self.frozen_reason_text = frozen_reason_text
        self.last_checkin_at = last_checkin_at


class _StubStatDef:
    def __init__(
        self, id: str, slug: str, name: str, icon: str, sort_order: int,
    ) -> None:
        self.id = id
        self.slug = slug
        self.name = name
        self.icon = icon
        self.sort_order = sort_order


class _InMemoryStatDefinitionRepo:
    def __init__(self) -> None:
        self._defs: dict[str, _StubStatDef] = {}

    def add(
        self, id: str, slug: str, name: str, icon: str, sort_order: int,
    ) -> None:
        self._defs[id] = _StubStatDef(id, slug, name, icon, sort_order)

    async def get_by_id(self, id: str):
        return self._defs.get(id)


# ── fixture: TestClient + character service override ───────────


def _build_test_client(
    *, user_stats_repo, user_status_repo, stat_definition_repo,
):
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.core.deps import get_character_service
    from app.core.security import TelegramUser
    from app.main import create_app
    from app.services.character_service import CharacterService

    get_settings.cache_clear()

    app = create_app()

    fake_character_service = CharacterService(
        user_stats_repo=user_stats_repo,
        user_status_repo=user_status_repo,
        stat_definition_repo=stat_definition_repo,
    )

    async def _fake_current_user_db():
        return TelegramUser(
            id=999,
            first_name="Alice",
            last_name=None,
            username="alice",
            language_code=None,
            is_premium=False,
            auth_date=int(datetime.now(tz=UTC).timestamp()),
        )

    from app.api.v1.users import current_user_db
    app.dependency_overrides[current_user_db] = _fake_current_user_db
    app.dependency_overrides[get_character_service] = (
        lambda: fake_character_service
    )

    return TestClient(app)


# ── tests ────────────────────────────────────────────────────────


def test_character_me_returns_payload_for_user_with_stats() -> None:
    """Happy: user с 3 stat-строк → payload корректный."""
    stats_repo = _InMemoryUserStatsRepo()
    status_repo = _InMemoryUserStatusRepo()
    sd_repo = _InMemoryStatDefinitionRepo()

    for i, (name, threshold, icon) in enumerate([
        ("На старте", 0, "🐣"),
        ("В потоке", 30, "🌊"),
        ("На волне", 100, "⚡"),
        ("В форме", 300, "🔥"),
        ("Режим зверя", 700, "🐺"),
    ]):
        status_repo.add(name, threshold, icon, i + 1)

    sd_repo.add("sd-int", "intelligence", "Интеллект", "🧠", 1)
    sd_repo.add("sd-str", "strength", "Сила", "💪", 2)
    sd_repo.add("sd-end", "endurance", "Выносливость", "🫁", 3)

    last_checkin = datetime.now(tz=UTC) - timedelta(days=1)
    stats_repo.add(999, "sd-int", value=58, is_frozen=False, last_checkin_at=last_checkin)
    stats_repo.add(999, "sd-str", value=12, is_frozen=False, last_checkin_at=None)
    stats_repo.add(999, "sd-end", value=0, is_frozen=True, last_checkin_at=None)

    client = _build_test_client(
        user_stats_repo=stats_repo,
        user_status_repo=status_repo,
        stat_definition_repo=sd_repo,
    )
    resp = client.get("/api/v1/character/me", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["total_value"] == 70  # 58 + 12 + 0 (frozen zero тоже суммируется)
    assert body["status"]["name"] == "В потоке"  # 70 → 30<=70<100 → «В потоке»
    assert body["status"]["icon"] == "🌊"
    assert body["status"]["next_threshold"] == 100
    assert body["status"]["next_status"] == "На волне"
    assert len(body["stats"]) == 3  # все 3 видны (zero+frozen показывается)

    by_slug = {s["stat_slug"]: s for s in body["stats"]}
    assert by_slug["intelligence"]["value"] == 58
    assert by_slug["strength"]["value"] == 12
    assert by_slug["endurance"]["value"] == 0
    assert by_slug["endurance"]["is_frozen"] is True
    assert by_slug["endurance"]["frozen_reason_text"] is not None

    # Pydantic ISO-сериализация datetime.
    assert by_slug["intelligence"]["last_checkin_at"] is not None
    assert by_slug["strength"]["last_checkin_at"] is None


def test_character_me_with_empty_user_returns_start_status_fallback() -> None:
    """У юзера нет stats → total=0, status=«На старте», stats=[]."""
    stats_repo = _InMemoryUserStatsRepo()
    status_repo = _InMemoryUserStatusRepo()
    sd_repo = _InMemoryStatDefinitionRepo()

    for i, (name, threshold, icon) in enumerate([
        ("На старте", 0, "🐣"),
        ("В потоке", 30, "🌊"),
        ("На волне", 100, "⚡"),
        ("В форме", 300, "🔥"),
        ("Режим зверя", 700, "🐺"),
    ]):
        status_repo.add(name, threshold, icon, i + 1)

    client = _build_test_client(
        user_stats_repo=stats_repo,
        user_status_repo=status_repo,
        stat_definition_repo=sd_repo,
    )
    resp = client.get("/api/v1/character/me", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body == {
        "total_value": 0,
        "status": {
            "name": "На старте",
            "icon": "🐣",
            "next_threshold": 30,
            "next_status": "В потоке",
        },
        "stats": [],
    }


def test_character_me_serializes_last_checkin_at_as_iso_string() -> None:
    """datetime на service-слое → ISO-строка на API-слое (Pydantic)."""
    stats_repo = _InMemoryUserStatsRepo()
    status_repo = _InMemoryUserStatusRepo()
    sd_repo = _InMemoryStatDefinitionRepo()

    status_repo.add("На старте", 0, "🐣", 1)
    status_repo.add("В потоке", 30, "🌊", 2)

    sd_repo.add("sd-1", "intelligence", "Интеллект", "🧠", 1)

    ts = datetime(2026, 8, 21, 17, 0, 0, tzinfo=UTC)
    stats_repo.add(999, "sd-1", value=42, is_frozen=False, last_checkin_at=ts)

    client = _build_test_client(
        user_stats_repo=stats_repo,
        user_status_repo=status_repo,
        stat_definition_repo=sd_repo,
    )
    resp = client.get("/api/v1/character/me", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    raw_lc = body["stats"][0]["last_checkin_at"]
    assert raw_lc in ("2026-08-21T17:00:00+00:00", "2026-08-21T17:00:00Z"), (
        f"Pydantic должно сериализовать datetime как ISO с tz (UTC). Got: {raw_lc}"
    )
