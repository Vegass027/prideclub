#!/usr/bin/env bash
# Sofia E2E: проверить что PAUSED+topup+catch отвергается через WAIVED маркер.
#
# Разведка 2026-08-16, коммит A 2026-08-17 (Pravki-no-deposit-waived-marker).
# ОДНОРАЗОВЫЙ скрипт — после успешного прогона удалить.
#
# Требования:
#   - ssh-доступ к прод-серверу через алиас privichki-prod
#   - Backend + worker развёрнуты с коммитом 9c32d6f (commit A)
#   - Текущее время MSK — НЕ пересекло 21:00 UTC (= 00:00 MSK следующего дня)
#   - Sofia (id=5361424459) — реальный кандидат с 3 memberships
#   - 𝔭𝖗𝖎𝖓𝖙 (id=7295309649) — второй аккаунт для catch-атаки
#
# Что проверяет:
#   - PAUSED юзер с deposit < penalty получает WAIVED-маркер при закрытии окна
#   - После topup → recompute → ACTIVE: catch за этот club_date отвергается
#   - Никаких Transaction с amount=0, баланс не меняется
#
# Cleanup: best-effort в Step 9, не убирает WAIVED-маркеры (отражают реальное
# системное состояние). Удаление — отдельная ручная операция.

set -euo pipefail

PROD="privichki-prod"
PG_USER="habits"
PG_DB="habits"
SOFIA_ID=5361424459
PRINT_USER=7295309649

# helpers/record_club_date.py — Python helper, вычисляет club_date в TZ клуба.
cat > /tmp/sofia_helper_record_date.py <<'PYEOF'
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import asyncio
from app.db.session import async_session_factory
from sqlalchemy import text

async def main():
    async with async_session_factory() as s:
        row = (await s.execute(text("SELECT timezone FROM habits WHERE is_active=true LIMIT 1"))).first()
        local = datetime.now(tz=timezone.utc).astimezone(ZoneInfo(row.timezone))
        print(local.date().isoformat())

asyncio.run(main())
PYEOF

# helpers/recompute_pause.py — вызывает MembershipService.recompute_pause_status
cat > /tmp/sofia_helper_recompute.py <<'PYEOF'
import asyncio
import sys
from app.db.session import async_session_factory
from app.services.membership_service import MembershipService
from app.repositories.user_repository import UserRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.habit_repository import HabitRepository

async def main(user_id: int):
    async with async_session_factory() as s:
        svc = MembershipService(
            session=s,
            habit_repo=HabitRepository(s),
            membership_repo=MembershipRepository(s),
            user_repo=UserRepository(s),
        )
        await svc.recompute_pause_status(user_id)
        await s.commit()

asyncio.run(main(int(sys.argv[1])))
PYEOF

# helpers/try_catch.py — воспроизводит то, что делает catch_violator handler.
# Использует параметризованные SQL-запросы (защита от SQL injection
# и от багов с f-string подстановкой в Python-код).
cat > /tmp/sofia_helper_catch.py <<'PYEOF'
import asyncio
import sys
from datetime import datetime, timezone
from app.db.session import async_session_factory
from app.services.penalty_service import PenaltyService
from app.repositories.membership_repository import MembershipRepository
from app.repositories.habit_repository import HabitRepository
from app.repositories.checkin_repository import CheckinRepository
from app.repositories.suspicious_pairs_repository import SuspiciousPairsRepository
from sqlalchemy import text
from app.core.exceptions import PenaltyAlreadyProcessedError

async def main(sofia_id: int, print_user_id: int, expected_club_date: str):
    async with async_session_factory() as s:
        violator_row = (await s.execute(
            text("SELECT id, habit_id FROM memberships WHERE user_id=:uid LIMIT 1"),
            {"uid": sofia_id},
        )).first()
        violator_id = violator_row.id
        habit_id_str = str(violator_row.habit_id)

        catcher_row = (await s.execute(
            text("SELECT id FROM memberships WHERE user_id=:uid LIMIT 1"),
            {"uid": print_user_id},
        )).first()
        catcher_id = catcher_row.id

        h_repo = HabitRepository(s)
        habit = await h_repo.get(habit_id_str)
        assert habit is not None, f"habit {habit_id_str} not found"

        club_date_now = habit.club_date(datetime.now(tz=timezone.utc))
        print(f"club_date at catch moment: {club_date_now}")
        print(f"club_date at cron moment:  {expected_club_date}")
        if str(club_date_now) != expected_club_date:
            print("WARNING: club_date changed since cron! Re-run needed.")
            return

        service = PenaltyService(
            session=s,
            habit_repo=h_repo,
            membership_repo=MembershipRepository(s),
            checkin_repo=CheckinRepository(s),
            suspicious_repo=SuspiciousPairsRepository(s),
        )
        try:
            penalty = await service.apply_catch(
                catcher_user_id=print_user_id,
                violator_membership_id=violator_id,
                club_date=club_date_now,
                catcher_membership_id=catcher_id,
            )
            print(f"FAIL: catch SUCCEEDED with amount={penalty.amount} — дыра НЕ закрыта!")
        except PenaltyAlreadyProcessedError as e:
            print(f"OK: catch rejected — PenaltyAlreadyProcessedError(code={e.code})")
        except Exception as e:
            print(f"UNEXPECTED: catch raised {type(e).__name__}: {e}")
            raise

asyncio.run(main(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]))
PYEOF

echo "=== STEP 0: Backup Sofia state ==="
ORIGINAL_DEPOSIT=$(ssh "$PROD" "docker exec habit-postgres psql -U $PG_USER -d $PG_DB -tAc \"SELECT deposit_balance FROM users WHERE id=$SOFIA_ID;\"")
echo "Original deposit: $ORIGINAL_DEPOSIT kopecks"

echo "=== STEP 1: Record current club_date ==="
ssh "$PROD" "docker cp /tmp/sofia_helper_record_date.py habit-backend:/tmp/"
CLUB_DATE=$(ssh "$PROD" "docker exec habit-backend python -B /tmp/sofia_helper_record_date.py")
echo "club_date for E2E: $CLUB_DATE"
echo "If anything fails after this, restart ONLY if current UTC time is past 21:00 (= 00:00 MSK next day)."

echo "=== STEP 2: Setup — set deposit=0, recompute → PAUSED ==="
ssh "$PROD" "docker exec habit-postgres psql -U $PG_USER -d $PG_DB -c \"
UPDATE users SET deposit_balance=0 WHERE id=$SOFIA_ID;
\""

ssh "$PROD" "docker cp /tmp/sofia_helper_recompute.py habit-backend:/tmp/"
ssh "$PROD" "docker exec habit-backend python -B /tmp/sofia_helper_recompute.py $SOFIA_ID"

echo "Sofia memberships after recompute:"
ssh "$PROD" "docker exec habit-postgres psql -U $PG_USER -d $PG_DB -c \"
SELECT m.id, h.title, m.status, u.deposit_balance
FROM memberships m
JOIN habits h ON h.id=m.habit_id
JOIN users u ON u.id=m.user_id
WHERE u.id=$SOFIA_ID
ORDER BY h.title;
\""
# EXPECTED: 3 строки status=paused, deposit=0

echo "=== STEP 3: Trigger cron close_catch_window ==="
TASK_RESULT=$(ssh "$PROD" "docker exec habit-worker celery -A worker.celery_app call worker.tasks.close_catch_window.run_for_active_habits 2>&1")
echo "Celery result: $TASK_RESULT"
echo "EXPECTED: summary содержит waived: <число> для каждого клуба где есть PAUSED юзеры"

echo "=== STEP 4: Verify WAIVED markers created for $CLUB_DATE ==="
WAIVED_COUNT=$(ssh "$PROD" "docker exec habit-postgres psql -U $PG_USER -d $PG_DB -tAc \"
SELECT COUNT(*) FROM penalties p
JOIN memberships m ON m.id=p.membership_id
WHERE m.user_id=$SOFIA_ID
  AND p.reason='waived_unable_to_pay'
  AND p.date='$CLUB_DATE';
\"")
echo "WAIVED markers created: $WAIVED_COUNT (expected: 3)"
if [[ "$WAIVED_COUNT" -ne 3 ]]; then
    echo "FAIL: expected 3 WAIVED markers, got $WAIVED_COUNT"
    echo "Sofia remains in test state. Manual cleanup required:"
    echo "  docker exec habit-postgres psql -U habits -d habits -c \"UPDATE users SET deposit_balance=$ORIGINAL_DEPOSIT WHERE id=$SOFIA_ID;\""
    exit 1
fi

echo "=== STEP 5: Verify no zero-amount Transactions, deposit unchanged ==="
ZERO_TX_COUNT=$(ssh "$PROD" "docker exec habit-postgres psql -U $PG_USER -d $PG_DB -tAc \"
SELECT COUNT(*) FROM transactions t
WHERE t.user_id=$SOFIA_ID
  AND t.created_at >= NOW() - INTERVAL '5 minutes'
  AND t.amount=0;
\"")
echo "Zero-amount transactions in last 5min: $ZERO_TX_COUNT (expected: 0)"
if [[ "$ZERO_TX_COUNT" -ne 0 ]]; then
    echo "FAIL: WAIVED marker created a zero-amount Transaction"
    exit 1
fi

CURRENT_DEPOSIT=$(ssh "$PROD" "docker exec habit-postgres psql -U $PG_USER -d $PG_DB -tAc \"SELECT deposit_balance FROM users WHERE id=$SOFIA_ID;\"")
echo "Deposit after cron: $CURRENT_DEPOSIT kopecks (expected: 0)"
if [[ "$CURRENT_DEPOSIT" -ne 0 ]]; then
    echo "FAIL: deposit changed during cron"
    exit 1
fi

echo "=== STEP 6: Top up Sofia (simulate payment) → recompute → ACTIVE ==="
ssh "$PROD" "docker exec habit-postgres psql -U $PG_USER -d $PG_DB -c \"
UPDATE users SET deposit_balance=25000 WHERE id=$SOFIA_ID;
\""

ssh "$PROD" "docker exec habit-backend python -B /tmp/sofia_helper_recompute.py $SOFIA_ID"

SOFIA_STATES=$(ssh "$PROD" "docker exec habit-postgres psql -U $PG_USER -d $PG_DB -tAc \"SELECT status FROM memberships WHERE user_id=$SOFIA_ID ORDER BY id;\"")
echo "Sofia states after topup: $SOFIA_STATES"
# EXPECTED: все active

echo "=== STEP 7: Try to catch Sofia via direct PenaltyService call ==="
ssh "$PROD" "docker cp /tmp/sofia_helper_catch.py habit-backend:/tmp/"
CATCH_RESULT=$(ssh "$PROD" "docker exec habit-backend python -B /tmp/sofia_helper_catch.py $SOFIA_ID $PRINT_USER $CLUB_DATE")
echo "$CATCH_RESULT"

if echo "$CATCH_RESULT" | grep -q "FAIL: catch SUCCEEDED"; then
    echo "TEST FAILED — дыра не закрыта"
    exit 1
fi
if ! echo "$CATCH_RESULT" | grep -q "OK: catch rejected"; then
    echo "TEST INCONCLUSIVE — результат не содержит ни FAIL, ни OK"
    exit 1
fi

echo "=== STEP 8: Verify catch attempt did not deduct from deposit ==="
DEPOSIT_AFTER=$(ssh "$PROD" "docker exec habit-postgres psql -U $PG_USER -d $PG_DB -tAc \"SELECT deposit_balance FROM users WHERE id=$SOFIA_ID;\"")
echo "Deposit after catch attempt: $DEPOSIT_AFTER kopecks (expected: 25000)"
if [[ "$DEPOSIT_AFTER" -ne 25000 ]]; then
    echo "FAIL: deposit was deducted despite WAIVED marker"
    exit 1
fi

echo "=== STEP 9: Cleanup — restore Sofia state ==="
ssh "$PROD" "docker exec habit-postgres psql -U $PG_USER -d $PG_DB -c \"
UPDATE users SET deposit_balance=$ORIGINAL_DEPOSIT WHERE id=$SOFIA_ID;
\""

echo "=== STEP 10: Cleanup temp files ==="
ssh "$PROD" "rm -f /tmp/sofia_helper_*.py"
rm -f /tmp/sofia_helper_*.py

echo "=== E2E PASSED ==="
echo "WAIVED marker защищает catch для PAUSED юзеров после topup. Дыра закрыта."
echo "ВАЖНО: этот скрипт одноразовый, после успешного прогона — rm apps/backend/scripts/e2e/sofia_waived_marker_test.sh"