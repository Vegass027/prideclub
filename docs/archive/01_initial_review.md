# Критические доработки и дополнения — по итогам ревью

Документ закрывает пункты обратной связи по проекту: юридическая модель денег,
часовые пояса, антифрод, race conditions, транзакционная целостность, сезоны,
онбординг, наблюдаемость. Дополняет файлы: koncepciya, arhitektura_tech_stek,
struktura_proekta, standarty_koda.

---

## 1. Юридическая модель денежных переводов (РЕШЕНО: Вариант A + B гибрид)

Прямые P2P-переводы "штраф → карман другого участника" убираются из механики.
Принятая модель:

- **Штраф нарушителя** списывается с его депозита и уходит в **общий призовой фонд клуба**
  (`prize_pool`), а не лично "спалившему".
- **"Спалившему"** начисляется не деньги, а **внутренние баллы/бонусы**:
  - +1 к счётчику "уловов" (влияет на лидерборд "Охотники");
  - бонусные дни подписки (например, +1 день за каждые 5 уловов);
  - приоритетный доступ к сезонным призам.
- **Призовой фонд** распределяется администрацией клуба **в конце сезона** между
  топ-участниками (по стрику и по числу уловов) — это юридически "выигрыш по итогам
  конкурса/акции", а не результат азартной игры между физлицами.
- Формальное описание: договор-оферта клуба квалифицирует депозит как "целевой взнос
  участника программы лояльности", а распределение призового фонда — как
  "поощрение по результатам программы", с прозрачным сроком и правилами в оферте.

### Что это меняет в схеме данных

```sql
-- было: catcher_reward прямому пользователю
-- стало:
CREATE TABLE penalties (
    id UUID PRIMARY KEY,
    membership_id UUID NOT NULL REFERENCES memberships(id),
    catcher_membership_id UUID REFERENCES memberships(id),  -- кто спалил (для статистики)
    amount INT NOT NULL,
    fund_share INT NOT NULL,           -- 100% штрафа уходит в фонд
    catcher_bonus_points INT DEFAULT 0, -- внутренние баллы, не деньги
    season_id UUID NOT NULL REFERENCES seasons(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (membership_id, created_at::date)  -- один штраф в день на участника
);
```

Виральность механики сохраняется ("заработай на чужой лени" остаётся верным по духу —
просто выигрыш реализуется через сезонный приз и статус, а не мгновенный денежный
перевод), при этом юридический риск снимается.

### Рекомендация по юр. лицу

- Старт: самозанятость, оферта на сайте/в Mini App с явным согласием при первом платеже.
- При росте оборота (выше лимита самозанятости) — переход на ИП УСН 6%.
- Депозит и подписка — разные назначения платежа в договоре: подписка = оплата доступа
  к сервису (невозвратна), депозит = обеспечительный взнос участника (возвратен при
  выходе за вычетом техкомиссии).

---

## 2. Часовые пояса — окно чек-ина считается по TZ клуба, не пользователя

```sql
ALTER TABLE habits ADD COLUMN timezone VARCHAR NOT NULL DEFAULT 'Europe/Moscow';
```

Правило: **"сегодня" и дедлайн чек-ина всегда считаются в часовом поясе клуба**,
а не участника. Это устраняет путаницу при переезде пользователя и делает дедлайн
одинаковым для всех участников одного клуба (что и логичнее для социальной механики —
все "спалиливают" друг друга в одном временном окне).

```python
# models/habit.py
class Habit:
    def is_within_checkin_window(self, message_sent_at_utc: datetime) -> bool:
        local_dt = message_sent_at_utc.astimezone(ZoneInfo(self.timezone))
        return self.checkin_window_start <= local_dt.time() <= self.checkin_window_end

    def today_in_club_tz(self) -> date:
        return datetime.now(ZoneInfo(self.timezone)).date()
```

Поле `users.timezone` остаётся, но используется только для **отображения** времени
в интерфейсе пользователя (например, "дедлайн через 2 часа"), не для расчёта
бизнес-правил.

---

## 3. Антифрод — обязательный раздел, минимум для MVP

### 3.1. Один чек-ин в сутки

```sql
CREATE UNIQUE INDEX uq_checkin_per_day
ON checkins (membership_id, date);
```

Повторная попытка чек-ина в тот же день — идемпотентно возвращает существующую запись,
а не создаёт дубликат.

### 3.2. Валидность доказательства

```python
def validate_proof_media(message: Message, proof_type: ProofType) -> bool:
    if proof_type == ProofType.VIDEO_NOTE:
        return message.video_note is not None and message.video_note.duration >= 3
    if proof_type == ProofType.PHOTO:
        return bool(message.photo) and message.photo[-1].file_size > 0
    if proof_type == ProofType.TEXT:
        return bool(message.text) and len(message.text.strip()) > 0
    return False
```

Дополнительно: проверка `message.date` — сообщение должно быть отправлено **сейчас**,
а не переслано (`forward_date is not None` → отклонять, чтобы нельзя было переслать
старый кружок).

### 3.3. Rate limit на "Спалить"

```python
# один пользователь не может "спалить" чаще, чем раз в 10 секунд
RATE_LIMIT_CATCH = "10/10s"  # 10 действий в 10 секунд — защита от скрипта/бота

async def check_catch_rate_limit(catcher_user_id: int, redis: Redis):
    key = f"catch_rate:{catcher_user_id}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 10)
    if count > 10:
        raise TooManyCatchAttemptsError()
```

### 3.4. Эвристика на сговор

- Если два участника зарегистрировались в один день **и** пришли по одной реферальной
  ссылке **и** никогда не "спаливают" друг друга при обоюдных нарушениях — помечать пару
  как `suspicious_pair` для ручной проверки администратором (не блокировать автоматически,
  чтобы не наказывать ложные срабатывания).
- Ограничение: один участник не может "спалить" одного и того же человека
  более N раз за сезон подряд без "спален быть" в ответ — простой сигнал для дальнейшего
  расследования, не авто-бан.

### 3.5. Ротация видимости "кого можно спалить"

Чтобы исключить сговор "ты меня не спаливаешь — я тебя не спаливаю", список нарушителей,
доступных для "спаливания", показывается **не всем участникам сразу**, а случайной
подвыборке (например, 30% участников в случайном порядке) в первые 2 часа окна — это
снижает эффективность договорных пар и добавляет элемент случайности в геймификацию.

---

## 4. Race conditions — блокировки и уникальные ограничения

### 4.1. Блокировка строки при списании депозита

```python
async def apply_penalty(self, membership_id: UUID, amount: int) -> Membership:
    async with self._db.begin():
        membership = await self._db.execute(
            select(Membership).where(Membership.id == membership_id).with_for_update()
        )
        membership = membership.scalar_one()

        if membership.deposit_balance < amount:
            amount = membership.deposit_balance  # не уходим в минус
            membership.status = MembershipStatus.PAUSED

        membership.deposit_balance -= amount
        await self._db.flush()
        return membership
```

`SELECT ... FOR UPDATE` гарантирует, что параллельные запросы на списание депозита
одного и того же `membership_id` обрабатываются последовательно, а не одновременно —
исключает уход баланса в минус при гонке.

### 4.2. Один штраф в день на участника (уникальный индекс)

```sql
CREATE UNIQUE INDEX uq_penalty_per_day
ON penalties (membership_id, (created_at::date));
```

Если два участника одновременно жмут "Спалить" — второй запрос получит конфликт
уникального индекса и корректно завершится ошибкой "уже обработано", без двойного штрафа.

---

## 5. Транзакционная целостность операций со штрафом

Все три записи (списание, начисление в фонд, создание строки в `penalties`) происходят
в **одной транзакции БД**:

```python
async def process_penalty(self, checkin_miss: CheckinMiss, catcher_membership_id: UUID):
    idempotency_key = f"penalty:{checkin_miss.membership_id}:{checkin_miss.date}"

    async with self._db.begin():
        existing = await self._penalty_repo.get_by_idempotency_key(idempotency_key)
        if existing:
            return existing  # задача уже обработана ранее — не задваиваем

        membership = await self._membership_repo.lock_for_update(checkin_miss.membership_id)
        amount = min(membership.deposit_balance, self._habit.penalty_amount)

        membership.deposit_balance -= amount
        await self._fund_repo.add_to_pool(self._habit.id, amount)

        penalty = await self._penalty_repo.create(
            membership_id=membership.id,
            catcher_membership_id=catcher_membership_id,
            amount=amount,
            idempotency_key=idempotency_key,
        )
        await self._transaction_repo.create(
            user_id=membership.user_id,
            type=TransactionType.PENALTY,
            amount=-amount,
            related_penalty_id=penalty.id,
            balance_after=membership.deposit_balance,
        )
        return penalty
```

**Idempotency key** строится из `membership_id + date` — если Celery повторно обработает
ту же задачу (например, после падения worker'а между шагами), повторный вызов вернёт
уже существующую запись, а не создаст штраф второй раз.

---

## 6. Доработанная таблица `transactions`

```sql
CREATE TABLE transactions (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    type VARCHAR NOT NULL,              -- subscription / deposit_topup / deposit_withdraw / penalty / prize
    amount INT NOT NULL,                -- может быть отрицательным
    balance_after INT NOT NULL,         -- баланс депозита после операции (для аудита)
    related_penalty_id UUID REFERENCES penalties(id),
    related_membership_id UUID REFERENCES memberships(id),
    idempotency_key VARCHAR UNIQUE,     -- защита от дублей платежей Telegram
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`idempotency_key` для платежей строится из `telegram_payment_charge_id`, который Telegram
присылает в `successful_payment` — гарантирует, что повторная доставка вебхука не создаст
задвоенное начисление.

---

## 7. Сезоны и лидерборды — схема данных

```sql
CREATE TABLE seasons (
    id UUID PRIMARY KEY,
    habit_id UUID NOT NULL REFERENCES habits(id),
    starts_at DATE NOT NULL,
    ends_at DATE NOT NULL,
    prize_pool INT NOT NULL DEFAULT 0,
    status VARCHAR NOT NULL DEFAULT 'active'  -- active / closed / paid_out
);

CREATE TABLE season_stats (
    season_id UUID NOT NULL REFERENCES seasons(id),
    membership_id UUID NOT NULL REFERENCES memberships(id),
    streak_days INT NOT NULL DEFAULT 0,
    total_penalties_caught INT NOT NULL DEFAULT 0,
    total_penalties_received INT NOT NULL DEFAULT 0,
    PRIMARY KEY (season_id, membership_id)
);

CREATE TABLE prizes (
    id UUID PRIMARY KEY,
    season_id UUID NOT NULL REFERENCES seasons(id),
    membership_id UUID NOT NULL REFERENCES memberships(id),
    amount INT NOT NULL,
    paid_out_at TIMESTAMPTZ
);
```

Правило: `checkins`, `penalties`, `transactions` — исторические таблицы, **никогда не
обнуляются**. Обнуляется только текущий рабочий срез в `season_stats`, который
пересчитывается инкрементально при каждом чек-ине/штрафе (не полным пересчётом истории).

---

## 8. AI-комендант — явно выносится в v2

Зафиксировано: AI-персонаж (мотивационные сообщения, "доска позора", дневная сводка)
**не входит в MVP**. Причины: требует отдельного сервиса, бюджета на LLM API, дополнительной
работы с контекстом и модерацией контента. В MVP эти функции временно закрываются простыми
статическими шаблонами сообщений от бота (например, "Сегодня 3 нарушения в клубе Планка"
без AI-генерации). Возврат к AI-коменданту — отдельный этап после стабилизации MVP.

---

## 9. Push-уведомления и обязательный /start боту

Ограничение Telegram: бот может писать пользователю в ЛС только после того, как
пользователь сам инициировал диалог (`/start`). Это делает **первый шаг онбординга
обязательным**, а не опциональным.

### Обновлённый сценарий онбординга

1. Пользователь переходит по ссылке/кнопке → открывается бот.
2. **Первое действие бота — потребовать `/start`** (стандартно уже происходит при первом
   открытии диалога), после чего бот сохраняет `user_id` и помечает
   `notifications_enabled = true`.
3. Далее бот открывает Mini App (WebApp button).
4. Если пользователь открыл Mini App **без** предварительного `/start` боту (например,
   через прямую ссылку) — Mini App показывает баннер "Нажмите /start у бота, чтобы получать
   уведомления о штрафах и дедлайнах" со ссылкой на диалог с ботом.
5. **Graceful degradation**: если `notifications_enabled = false`, Mini App продолжает
   работать полностью (чек-ины, лидерборд), но статус штрафов/наград пользователь увидит
   только при следующем открытии приложения, без push-уведомления.

```sql
ALTER TABLE users ADD COLUMN notifications_enabled BOOLEAN NOT NULL DEFAULT false;
```

### Согласие с офертой

При первом платеже (подписка или депозит) Mini App показывает нативный `showConfirm` с
текстом согласия на оферту и ссылкой на полный текст — согласие фиксируется в БД
(`users.accepted_offer_at`) перед вызовом Telegram Payments.

---

## 10. Наблюдаемость — продуктовые метрики (дополнение к Prometheus/Sentry)

| Метрика | Как считается | Цель |
|---|---|---|
| Daily Active Check-ins | count(checkins) за день / count(active memberships) | Здоровье продукта |
| Конверсия "вступление → 1-й чек-ин" | % memberships с checkins в первые 24ч после joined_at | Качество онбординга |
| Конверсия "1-й чек-ин → 7 дней подряд" | % достигших streak_days >= 7 | Удержание |
| Средний размер и частота штрафов | avg(amount), count(penalties) / active memberships | Здоровье дисциплины |
| SLA обработки чек-ина | время между message.date и записью в checkins | Технический SLA (цель < 5 сек) |
| Drop-off по экранам | события открытия каждого экрана Mini App | Поиск узких мест в UX |

Эти метрики логируются как структурированные события (например, через Segment-подобный
подход или напрямую в отдельную таблицу `analytics_events`) и визуализируются в Grafana
рядом с инфраструктурными метриками.

---

## 11. Версионирование API

- Префикс `/api/v1` остаётся с первого дня.
- Правило: при выходе `/api/v2` версия `v1` поддерживается минимум 3 месяца параллельно,
  после чего помечается deprecated в документации и логирует warning при каждом вызове.
- Breaking changes никогда не вносятся в существующую версию — только через новую.

---

## 12. Конфигурация тарифов — не хардкод

```sql
CREATE TABLE pricing_rules (
    id UUID PRIMARY KEY,
    habit_rank INT NOT NULL,       -- 1, 2, 3, 4+
    price_month INT NOT NULL,
    active_from TIMESTAMPTZ NOT NULL DEFAULT now(),
    active_to TIMESTAMPTZ
);
```

Цена за N-ю привычку читается из этой таблицы, а не из констант кода — маркетинг может
A/B-тестировать цены без релиза (через админку или прямую вставку в таблицу).

---

## 13. Обновлённый порядок разработки MVP (с учётом доработок)

1. Юридическая модель + оферта — до старта кодирования денежной механики.
2. Схема БД с учётом: TZ клуба, уникальные индексы, `season_stats`, `pricing_rules`,
   доработанная `transactions`.
3. Onboarding: `/start` боту как обязательный шаг + graceful degradation без уведомлений.
4. Backend: чек-ины с антифрод-проверками (валидность медиа, один чек-ин в день).
5. Backend: штрафы с блокировками `FOR UPDATE`, идемпотентностью, начислением в
   призовой фонд (не P2P).
6. Frontend: экраны "Сегодня", "Участники" (с ротацией видимости для "Спалить"),
   "Лидерборд", "Баланс".
7. Платежи: Telegram Payments с `idempotency_key` на `telegram_payment_charge_id`.
8. Наблюдаемость: продуктовые метрики с первого релиза, не постфактум.
9. AI-комендант — сознательно отложен на v2.

Такой порядок закрывает все критичные риски (юридический, антифрод, целостность денег)
до того, как на проект придут первые платящие пользователи.
