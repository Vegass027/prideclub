import { useState } from "react";
import { useParams } from "react-router-dom";
import { useToday, useHabitSse, useWallet } from "@/shared/hooks";
import { formatKopecks } from "@/shared/utils/format";
import { computeSubState } from "@/shared/utils/subscriptionState";
import { missingKopecks } from "@/shared/utils/topupPresets";
import { BottomNav } from "@/shared/ui/BottomNav";
import { Button } from "@/shared/ui/Button";
import { EmptyState } from "@/shared/ui/EmptyState";
import { HabitNav } from "@/shared/ui/HabitNav";
import { JoinPayModal } from "@/shared/ui/JoinPayModal";
import { PageHeader } from "@/shared/ui/PageHeader";
import { ScreenLayout } from "@/shared/ui/ScreenLayout";
import { Skeleton } from "@/shared/ui/Skeleton";
import { StatusBadge } from "@/shared/ui/StatusBadge";
import { TopUpModal } from "@/shared/ui/TopUpModal";
import { hapticImpact, openTelegramLink } from "@/shared/telegram/tma";
import { openCheckinTopic, openChatRoot } from "@/shared/telegram/topicLink";
import type { ProofType } from "@/shared/types";

type ProofCfg = { emoji: string; title: string; hint: string };

const PROOF_LABELS: Record<ProofType, ProofCfg> = {
  video_note: {
    emoji: "🎥",
    title: "Видео-кружочек",
    hint: "Отправь видео-кружочек ≥ 3 сек в чат клуба. Бот примет его автоматически.",
  },
  photo: {
    emoji: "📸",
    title: "Фото",
    hint: "Отправь фото в чат клуба. Бот примет его автоматически.",
  },
  text: {
    emoji: "✍️",
    title: "Текст",
    hint: "Отправь текстовое подтверждение в чат клуба.",
  },
};

function resolveProofTypes(habit: { proof_types: ProofType[]; proof_type: ProofType }): ProofType[] {
  if (habit.proof_types.length > 0) return habit.proof_types;
  return [habit.proof_type];
}

function Stat({
  label,
  value,
  icon,
  tone,
}: {
  label: string;
  value: number | string;
  icon: string;
  tone: string;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-lg" aria-hidden="true">{icon}</span>
      <div className="flex flex-col leading-tight">
        <span className={`text-base font-bold tabular-nums ${tone}`}>{value}</span>
        <span className="text-xs text-muted">{label}</span>
      </div>
    </div>
  );
}

export function TodayPage() {
  const { habitId } = useParams<{ habitId: string }>();
  const { data, isLoading, isError, error, refetch } = useToday(habitId);

  // Pravki-deposit-sse.md §Z-4.1/Z-4.3: используем useWallet для блокировки
  // кнопки «Сделать чек-ин» если deposit < penalty этого клуба.
  const { data: wallet } = useWallet();
  const [topupOpen, setTopupOpen] = useState(false);
  // Pravki-subscription-2026-08-17 §Z-22: модалка продления подписки (smart renew).
  const [renewOpen, setRenewOpen] = useState(false);

  // Real-time: держим ["today", habitId] в кэше актуальным через SSE
  // multiplex (Pravki Items 7+8+9). useToday даёт первый снимок при загрузке,
  // useHabitSse — обновления по:
  //  - checkin.accepted/rejected (user-stream, personal)
  //  - catch (habit-stream broadcast — у жертвы пропадает бейдж «Поймать»)
  //  - you_were_caught (user-stream — жертва меняет статус)
  // Без polling, без ручного refetch, без лишнего GET на каждый mount.
  // React Query сам управляет stale-инвалидацией через staleTime: 30_000
  // в useToday.
  useHabitSse(habitId);

  const handleOpenChat = () => {
    if (!data) return;
    hapticImpact("medium");
    const { habit } = data;
    const url =
      habit.telegram_invite_link ||
      `https://t.me/c/${String(habit.chat_id).replace(/^-100/, "")}`;
    openTelegramLink(url);
  };

  if (isLoading) {
    return (
      <ScreenLayout>
        <PageHeader title="Сегодня" back backTo="/profile" />
        <Skeleton className="h-24 w-full" />
        <div className="mt-4">
          <Skeleton className="h-40 w-full" />
        </div>
        <HabitNav habitId={habitId!} />
      </ScreenLayout>
    );
  }

  if (isError || !data) {
    return (
      <ScreenLayout>
        <PageHeader title="Сегодня" back backTo="/profile" />
        <EmptyState
          icon="⚠️"
          title="Не удалось загрузить статус"
          description={String(error ?? "Неизвестная ошибка")}
          action={<Button onClick={() => refetch()}>Повторить</Button>}
        />
        <BottomNav />
      </ScreenLayout>
    );
  }

  const { habit, membership, checkin } = data;
  const allowedProofTypes = resolveProofTypes(habit);
  const singleProof = allowedProofTypes.length === 1;
  const primaryCfg: ProofCfg =
    PROOF_LABELS[allowedProofTypes[0]] ?? PROOF_LABELS.text;

  // Pravki-deposit-sse.md §Z-4.3: can_checkin из wallet-кеша.
  // Если wallet ещё не загружен — оптимистично считаем что can_checkin=true
  // (бэк уже верифицировал membership при GET /habits/{id}/today).
  const walletClub = wallet?.active_clubs.find((c) => c.habit_id === habit.id);
  const canCheckin = walletClub?.can_checkin ?? true;
  const depositMissing = walletClub
    ? missingKopecks(walletClub.penalty_amount, wallet?.deposit_balance ?? 0)
    : 0;

  // Pravki-subscription-2026-08-17: состояние подписки для бейджа/баннера.
  // Источник — walletClub.subscription_until (если есть), иначе membership.
  const subState = computeSubState(
    walletClub?.subscription_until ?? membership?.subscription_until ?? null,
    habit.timezone,
  );

  return (
    <ScreenLayout>
      <PageHeader
        title={`〖${habit.title}〗`}
        back
        backTo="/profile"
      />

      {habit.description && (
        <section className="mb-3 rounded-card border border-white/5 bg-surface p-3 text-xs text-muted">
          {habit.description}
        </section>
      )}

      {habit.photo_url ? (
        <div className="mb-3 flex items-center justify-center rounded-card border border-white/5 bg-canvas/60 p-2">
          <img
            src={habit.photo_url}
            alt={habit.title}
            className="block max-h-72 w-full object-contain"
            loading="lazy"
          />
        </div>
      ) : null}

      <section className="rounded-card border border-white/5 bg-surface p-4 shadow-card">
        <div className="mb-3 grid grid-cols-2 gap-3">
          <Stat
            label="дн. всего"
            value={checkin.checkin_count}
            icon="📅"
            tone="text-text"
          />
          <Stat
            label="дн. подряд"
            value={checkin.streak_days}
            icon="🔥"
            tone={checkin.streak_days > 0 ? "text-text" : "text-muted"}
          />
          <Stat
            label="пойман"
            value={checkin.penalties_count}
            icon="🎯"
            tone={checkin.penalties_count > 0 ? "text-red-400" : "text-muted"}
          />
          <Stat
            label="потерял"
            value={`${(checkin.penalties_total / 100).toFixed(0)} ₽`}
            icon="💸"
            tone={checkin.penalties_total > 0 ? "text-red-400" : "text-muted"}
          />
        </div>
        <p className="text-sm text-muted">
          Окно чек-ина: <strong className="text-text">{habit.checkin_window_start.slice(0, 5)}–{habit.checkin_window_end.slice(0, 5)}</strong>
        </p>
        <div className="mt-3 flex items-center justify-between border-t border-white/5 pt-3">
          <span className="text-sm text-muted">Ежедневное задание:</span>
          <StatusBadge status={checkin.status} />
        </div>
      </section>

      <section className="mt-4 rounded-card border-2 border-primary/30 bg-primary/5 p-4">
        <div className="mb-2 flex items-center gap-3">
          <span className="text-3xl" aria-hidden="true">
            {singleProof ? primaryCfg.emoji : "🎯"}
          </span>
          <h3 className="text-base font-semibold text-text">
            {singleProof ? primaryCfg.title : "Чек-ин — любой из типов"}
          </h3>
        </div>
        {singleProof ? (
          <p className="mb-3 text-sm text-muted">{primaryCfg.hint}</p>
        ) : (
          <>
            <p className="mb-3 text-sm text-muted">
              Клуб принимает несколько типов подтверждений. Подходит любой:
            </p>
            <ul className="mb-3 space-y-2">
              {allowedProofTypes.map((t) => {
                const cfg = PROOF_LABELS[t] ?? PROOF_LABELS.text;
                return (
                  <li key={t} className="flex items-start gap-2 text-sm">
                    <span className="text-base" aria-hidden="true">
                      {cfg.emoji}
                    </span>
                    <span>
                      <strong className="text-text">{cfg.title}</strong>
                      <span className="block text-xs text-muted">{cfg.hint}</span>
                    </span>
                  </li>
                );
              })}
            </ul>
          </>
        )}

        {/* Pravki-subscription-2026-08-17 §Z-22: бейдж/баннер подписки.
            Идёт ПЕРЕД депозитным баннером — это разные проблемы с разными
            recovery-действиями ("продли подписку" vs "пополни депозит"),
            показываем оба одновременно если оба активны. */}
        {subState && subState.kind === "soon" && (
          <div className="mb-3 rounded-card border border-warning/30 bg-warning/10 p-3 text-sm">
            <strong className="block text-warning">
              {subState.daysLeft === 1
                ? "⚠️ Подписка закончится через 1 день"
                : "⚠️ Подписка закончится через 2 дня"}
            </strong>
            <span className="mt-1 block text-xs text-muted">
              Продли заранее в мини-аппе, чтобы не потерять доступ к чекинам.
            </span>
          </div>
        )}
        {subState && subState.kind === "expired" && (
          <div className="mb-3 rounded-card border-2 border-danger/30 bg-danger/10 p-3 text-sm">
            <strong className="block text-danger">
              🚫 Подписка окончена
            </strong>
            <span className="mt-1 block text-xs text-muted">
              Чек-ин невозможен до продления участия.
            </span>
          </div>
        )}

        {/* Pravki-deposit-sse.md §Z-4.3: блокировка чек-ина при недостаточном депозите. */}
        {!canCheckin && walletClub && (
          <div className="mb-3 rounded-card border border-warning/30 bg-warning/10 p-3 text-sm">
            <strong className="block text-warning">
              ⚠️ Для продолжения участия нужно ≥ {formatKopecks(walletClub.penalty_amount)} на депозите.
            </strong>
            <span className="mt-1 block text-xs text-muted">
              Сейчас: {formatKopecks(wallet?.deposit_balance ?? 0)}.{" "}
              Не хватает: {formatKopecks(depositMissing)}.
            </span>
          </div>
        )}

        <div className="flex flex-col gap-2">
          {habit.checkin_topic_thread_id !== null ? (
            <Button
              onClick={() => {
                hapticImpact("medium");
                openCheckinTopic(habit.chat_id, habit.checkin_topic_thread_id);
              }}
              className="w-full"
              disabled={!canCheckin}
              aria-disabled={!canCheckin}
            >
              🎬 Сделать чек-ин
            </Button>
          ) : (
            <Button onClick={handleOpenChat} className="w-full">
              Открыть чат клуба
            </Button>
          )}
          {habit.chat_topic_thread_id !== null && (
            <Button
              variant="secondary"
              onClick={() => {
                hapticImpact("light");
                openCheckinTopic(habit.chat_id, habit.chat_topic_thread_id);
              }}
              className="w-full"
            >
              💬 Перейти в чат
            </Button>
          )}
          {!canCheckin && walletClub && (
            <Button
              variant="secondary"
              onClick={() => {
                hapticImpact("medium");
                setTopupOpen(true);
              }}
              className="w-full"
            >
              💰 Пополнить депозит
            </Button>
          )}
          {/* Pravki-subscription-2026-08-17 §Z-22: кнопка продления подписки.
              Показывается когда soon/expired (а не ok). Не показывается
              если уже висит баннер депозита (canCheckin=false) — там
              пользователю и так хватает действий. Но НЕ зависит от canCheckin:
              юзер может иметь полный депозит но истёкшую подписку, и ему
              нужна кнопка продления. */}
          {subState && (subState.kind === "soon" || subState.kind === "expired") && (
            <Button
              variant="secondary"
              onClick={() => {
                hapticImpact("medium");
                setRenewOpen(true);
              }}
              className="w-full"
            >
              🔄 Продлить подписку
            </Button>
          )}
        </div>
      </section>

      {(habit.telegram_invite_link || habit.chat_id !== 0) && (
        <section className="mt-4 rounded-card border border-white/10 bg-surface p-4">
          {membership.status === "active" ? (
            <>
              <h3 className="mb-2 text-sm font-semibold text-text">
                Клуб в Telegram
              </h3>
              <Button
                disabled
                aria-disabled="true"
                className="w-full"
              >
                ❤️ Вы состоите в клубе
              </Button>
              <p className="mt-2 text-xs text-muted">
                {habit.telegram_invite_link
                  ? "Нажмите на ссылку ниже, чтобы перейти в группу."
                  : "Группа клуба привязана к вашему чату."}
              </p>
              {habit.telegram_invite_link && (
                <button
                  type="button"
                  onClick={() => {
                    hapticImpact("light");
                    openChatRoot(
                      habit.chat_id,
                      habit.telegram_invite_link ?? null,
                    );
                  }}
                  className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-md border border-white/10 bg-surface px-4 py-2 text-sm font-medium text-text transition hover:border-white/20"
                >
                  💬 Открыть группу
                </button>
              )}
            </>
          ) : membership.status === "paused" ? (
            // Fix follow-up (feature/paused-member-ux): paused-юзер ТОЖЕ состоит
            // в клубе — membership не LEFT, есть subscription_until. Раньше эта
            // ветка попадала в "иначе" и показывала активную кнопку "Присоединиться",
            // что вводило в заблуждение (юзер уже в клубе, просто на паузе из-за
            // пустого депозита). Теперь — disabled badge с пояснением + ссылка на группу.
            <>
              <h3 className="mb-2 text-sm font-semibold text-text">
                Клуб в Telegram
              </h3>
              <Button
                disabled
                aria-disabled="true"
                className="w-full"
              >
                ⏸ Участие на паузе
              </Button>
              <p className="mt-2 text-xs text-muted">
                Пополни депозит на баннере выше, чтобы вернуться к чек-инам.
                Чат клуба по-прежнему доступен ниже.
              </p>
              {habit.telegram_invite_link && (
                <button
                  type="button"
                  onClick={() => {
                    hapticImpact("light");
                    openChatRoot(
                      habit.chat_id,
                      habit.telegram_invite_link ?? null,
                    );
                  }}
                  className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-md border border-white/10 bg-surface px-4 py-2 text-sm font-medium text-text transition hover:border-white/20"
                >
                  💬 Открыть группу
                </button>
              )}
            </>
          ) : (
            // LEFT (или любой другой не-ACTIVE/PAUSED статус): юзер действительно
            // не состоит в клубе. Показываем join-flow.
            <>
              <h3 className="mb-2 text-sm font-semibold text-text">
                Клуб в Telegram
              </h3>
              <p className="mb-3 text-xs text-muted">
                Чтобы бот принимал твои чек-ины и участники могли тебя
                «поймать», нужно вступить в группу клуба в Telegram.
              </p>
              <Button
                onClick={() => {
                  hapticImpact("medium");
                  openChatRoot(
                    habit.chat_id,
                    habit.telegram_invite_link ?? null,
                  );
                }}
                className="w-full"
              >
                👋 Присоединиться к клубу
              </Button>
            </>
          )}
        </section>
      )}

      <section className="mt-4 rounded-card border border-white/5 bg-surface p-4 text-sm text-muted">
        <h3 className="mb-2 text-sm font-semibold text-text">Что дальше</h3>
        <ul className="list-disc space-y-1 pl-5">
          <li>Бот примет твоё подтверждение автоматически.</li>
          <li>Пропустишь окно — штраф {formatKopecks(habit.penalty_amount)} уйдёт в призовой фонд клуба.</li>
          <li>Участники могут «поймать» тебя, если ты не отметился — те же деньги в фонд.</li>
        </ul>
      </section>

      {checkin.status === "missed" && (
        <section className="mt-4 rounded-card border border-danger/30 bg-danger/10 p-4 text-sm">
          <strong className="block text-danger">Сегодня пропуск.</strong>
          {/* Pravki-paused-window-open-2026-08-14: текст о штрафе показывается
              только если penalty_for_today_kopecks > 0. До этого поля UI
              показывал "Штраф уже списан в призовой фонд" для ЛЮБОГО missed,
              что было ложью — apply_window_expired при balance=0 возвращает
              None без создания Penalty (см. apps/backend/app/services/
              penalty_service.py:apply_window_expired, строки ~254-258). */}
          {checkin.penalty_for_today_kopecks > 0 ? (
            <span className="text-muted">
              Штраф {formatKopecks(checkin.penalty_for_today_kopecks)} списан в призовой фонд клуба.
            </span>
          ) : (
            <span className="text-muted">
              Пропуск сегодня. Штраф не списан — депозит пуст, ловить некому.
            </span>
          )}
        </section>
      )}

      {/* Pravki-bug-fixes §Z-21 (caught badge): жертва поимки за сегодня.
          Отдельная ветка от missed — текст другой потому что сценарий разный:
          - missed = cron `close_catch_window` списал депозит (никто не ловил).
          - caught = другой участник поймал, штраф ушёл в приз-фонд. */}
      {checkin.status === "caught" && (
        <section className="mt-4 rounded-card border border-danger/30 bg-danger/10 p-4 text-sm">
          <strong className="block text-danger">Вас поймали.</strong>
          <span className="text-muted">
            Вы не выполнили ежедневное задание вовремя. Штраф списан в призовой фонд клуба.
          </span>
        </section>
      )}

      {/* Pravki-bug-fixes §Z-19 (joiner-late protection):
          юзер вступил в клуб сегодня ПОСЛЕ checkin_window_end.
          Нейтральный тон (не штрафной как missed, не зелёный как done).
          Без мутации депозита и без CTA. */}
      {checkin.status === "joined_late" && (
        <section className="mt-4 rounded-card border border-muted/30 bg-muted/10 p-4 text-sm">
          <strong className="block text-text">Вы вступили после чек-ина.</strong>
          <span className="text-muted">
            Следующая отметка — завтра.
          </span>
        </section>
      )}

      <HabitNav habitId={habit.id} />

      <TopUpModal
        open={topupOpen}
        onClose={() => setTopupOpen(false)}
        defaultAmount={
          walletClub
            ? missingKopecks(walletClub.penalty_amount, wallet?.deposit_balance ?? 0)
            : undefined
        }
      />

      {/* Pravki-subscription-2026-08-17: модалка продления подписки (smart renew).
          Открывается при клике на "🔄 Продлить подписку" в баннере soon/expired.
          Если депозит уже достаточный — mode="renew-only" (списываем только
          price_month). Если нет — mode="full" с предзаполненным deposit_amount. */}
      <JoinPayModal
        open={renewOpen}
        onClose={() => setRenewOpen(false)}
        onSuccess={() => {
          // После успешного продления — refetch всех зависимых данных.
          // Wallet (canCheckin + subscription_until) и today (membership).
          refetch();
        }}
        habit={{
          id: habit.id,
          title: habit.title,
          penalty_amount: habit.penalty_amount,
          price_month: habit.price_month,
        }}
        mode={
          walletClub && walletClub.can_checkin ? "renew-only" : "full"
        }
      />
    </ScreenLayout>
  );
}
