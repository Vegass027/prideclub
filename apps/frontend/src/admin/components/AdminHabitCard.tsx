import { Link } from "react-router-dom";
import type { AdminHabit } from "../api";
import { useStatDefinitions } from "../hooks";

interface AdminHabitCardProps {
  habit: AdminHabit;
  onToggle: (habitId: string, nextActive: boolean) => void;
  onDelete: (habitId: string) => void;
  onRestore: (habitId: string) => void;
  onPermanentDelete: (habitId: string) => void;
  busy: boolean;
}

const formatRub = (kopecks: number): string => {
  const rub = Math.round(kopecks / 100);
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 0,
  }).format(rub);
};

export function AdminHabitCard({
  habit,
  onToggle,
  onDelete,
  onRestore,
  onPermanentDelete,
  busy,
}: AdminHabitCardProps) {
  const isArchived = habit.archived_at !== null;
  // Phase 3 v2 Task 3.8: stat_name/stat_icon УБРАНЫ из AdminHabit. Резолвим
  // через каталог stat_definitions (тот же хук, что в HabitCreate/Edit).
  // Fallback "🔥 Характеристика" — null UUID или ещё не загружен каталог.
  const { data: statsData } = useStatDefinitions();
  const statDef = statsData?.items.find((s) => s.id === habit.stat_definition_id);
  const stat = statDef ? `${statDef.icon ?? "🔥"} ${statDef.name}` : "🔥 Характеристика";
  const window = `${habit.checkin_window_start.slice(0, 5)}–${habit.checkin_window_end.slice(0, 5)}`;

  const handleArchive = () => {
    const ok = (globalThis as unknown as { confirm?: (msg: string) => boolean }).confirm?.(
      `Переместить клуб «${habit.title}» в архив? Клуб перестанет показываться участникам.`,
    );
    if (ok !== false) onDelete(habit.id);
  };

  const handlePermanent = () => {
    const ok = (globalThis as unknown as { confirm?: (msg: string) => boolean }).confirm?.(
      `Удалить клуб «${habit.title}» НАВСЕГДА?\n\nДанные клуба, его медиа и привязка к Telegram-группе будут удалены без возможности восстановления.`,
    );
    if (ok !== false) onPermanentDelete(habit.id);
  };

  return (
    <article className="overflow-hidden rounded-card border border-white/5 bg-surface">
      {habit.photo_url ? (
        <div className="flex w-full items-center justify-center bg-canvas/60">
          <img
            src={habit.photo_url}
            alt={habit.title}
            className="block max-h-48 w-full object-contain"
            loading="lazy"
          />
        </div>
      ) : (
        <div
          className="flex h-24 w-full items-center justify-center bg-gradient-to-br from-primary/30 to-primary/5 text-3xl"
          aria-hidden="true"
        >
          🎯
        </div>
      )}

      <div className="p-4">
        <header className="mb-3 flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-base font-semibold text-text">
              〖{habit.title}〗
            </h3>
            {habit.description && (
              <p className="mt-0.5 line-clamp-2 text-xs text-muted">{habit.description}</p>
            )}
          </div>
          <span
            className={`shrink-0 rounded-full border px-2 py-0.5 text-xs ${
              isArchived
                ? "border-danger/30 bg-danger/10 text-danger"
                : habit.is_active
                  ? "border-success/30 bg-success/10 text-success"
                  : "border-muted/30 bg-muted/10 text-muted"
            }`}
          >
            {isArchived ? "В архиве" : habit.is_active ? "Активен" : "Скрыт"}
          </span>
        </header>

        <ul className="mb-3 space-y-1 text-xs">
          <li className="flex gap-2"><span aria-hidden="true">•</span><span className="text-muted">Характеристика:</span> <strong className="text-text">{stat}</strong></li>
          <li className="flex gap-2"><span aria-hidden="true">•</span><span className="text-muted">Окно чек-ина:</span> <strong className="text-text">{window}</strong></li>
          <li className="flex gap-2"><span aria-hidden="true">•</span><span className="text-muted">Часовой пояс:</span> <strong className="text-text">{habit.timezone}</strong></li>
          <li className="flex gap-2"><span aria-hidden="true">•</span><span className="text-muted">Доказательство:</span> <strong className="text-text">{habit.proof_types.map((t) => t === "video_note" ? "видео-кружок" : t === "photo" ? "фото" : "текст").join(" или ")}</strong></li>
          <li className="flex gap-2"><span aria-hidden="true">•</span><span className="text-muted">Цена в месяц:</span> <strong className="text-text">{formatRub(habit.price_month)}</strong></li>
          <li className="flex gap-2"><span aria-hidden="true">•</span><span className="text-muted">Штраф за пропуск:</span> <strong className="text-text">{formatRub(habit.penalty_amount)}</strong></li>
          {/* Pravki-catcher-deposit (Phase 1 Task 1.6b, 2026-08-21): сумма ловцу
              отображается только если > 0 (default = всё в фонд — старая логика,
              владельцу незачем видеть неактивную настройку). Warning если
              >= штрафа — clamp на бэкенде в apply_catch. */}
          {habit.catcher_amount_kopecks > 0 && (
            <li className="flex gap-2">
              <span aria-hidden="true">•</span>
              <span className="text-muted">Ловцу за поимку:</span>
              <strong className="text-text">{formatRub(habit.catcher_amount_kopecks)}</strong>
              {habit.catcher_amount_kopecks >= habit.penalty_amount && (
                <span
                  className="text-warning"
                  title="Если баланс нарушителя меньше штрафа, доли считаются от фактически списанной суммы"
                >
                  ⚠️ вся сумма штрафа уходит ловцу
                </span>
              )}
            </li>
          )}
          <li className="flex gap-2"><span aria-hidden="true">•</span><span className="text-muted">Призовой фонд:</span> <strong className="text-text">{formatRub(habit.prize_pool)}</strong></li>
          <li className="flex gap-2"><span aria-hidden="true">•</span><span className="text-muted">Участников:</span> <strong className="text-text">{habit.active_members_count}</strong>{habit.member_limit !== null && (<span className="text-muted"> / {habit.member_limit}</span>)}</li>
          {habit.stat_gain_per_checkin > 0 && (
            <li className="flex gap-2"><span aria-hidden="true">•</span><span className="text-muted">+{habit.stat_gain_per_checkin} {stat} за чек-ин</span></li>
          )}
          {habit.stat_loss_per_miss > 0 && (
            <li className="flex gap-2"><span aria-hidden="true">•</span><span className="text-muted">−{habit.stat_loss_per_miss} {stat} за пропуск</span></li>
          )}
        </ul>

        <div className="mt-3 flex items-stretch gap-2">
        <Link
          to={`/habits/${habit.id}/edit`}
          aria-label={`Изменить ${habit.title}`}
          className="flex-1 inline-flex min-h-[44px] items-center justify-center rounded-card border border-white/10 bg-surface px-3 py-2 text-sm font-medium text-text transition hover:border-white/20"
        >
          Изменить
        </Link>

        {!isArchived && (
          <div className="flex-1 flex items-center justify-center">
            <IosSwitch
              checked={habit.is_active}
              disabled={busy}
              onChange={(next) => onToggle(habit.id, next)}
              ariaLabel={`Активность клуба ${habit.title}`}
            />
          </div>
        )}

        {!isArchived && (
          <button
            type="button"
            onClick={handleArchive}
            disabled={busy}
            aria-busy={busy}
            aria-label={`Переместить клуб ${habit.title} в архив`}
            title="В архив"
            className="flex-1 inline-flex min-h-[44px] min-w-[44px] items-center justify-center gap-2 rounded-card border border-danger/30 bg-danger/10 px-3 py-2 text-sm font-medium text-danger transition hover:bg-danger/20 disabled:opacity-50"
          >
            <TrashIcon />
            <span>В архив</span>
          </button>
        )}

        {isArchived && (
          <button
            type="button"
            onClick={() => onRestore(habit.id)}
            disabled={busy}
            aria-busy={busy}
            className="flex-1 inline-flex min-h-[44px] items-center justify-center rounded-card border border-success/30 bg-success/10 px-3 py-2 text-sm font-medium text-success transition hover:bg-success/20 disabled:opacity-50"
          >
            Восстановить
          </button>
        )}

        {isArchived && (
<button
            type="button"
            onClick={handlePermanent}
            disabled={busy}
            aria-busy={busy}
            aria-label={`Удалить клуб ${habit.title} навсегда`}
            title="Удалить навсегда"
            className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center gap-2 rounded-card border border-danger/40 bg-danger/20 px-3 py-2 text-danger transition hover:bg-danger/30 disabled:opacity-50"
          >
            <FireIcon />
          </button>
        )}
      </div>
      </div>
    </article>
  );
}

function IosSwitch({
  checked,
  disabled,
  onChange,
  ariaLabel,
}: {
  checked: boolean;
  disabled: boolean;
  onChange: (next: boolean) => void;
  ariaLabel: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition-colors duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 ${
        checked ? "bg-success" : "bg-muted/40"
      } ${disabled ? "opacity-50 cursor-not-allowed" : ""}`}
    >
      <span
        className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform duration-200 ${
          checked ? "translate-x-6" : "translate-x-1"
        }`}
      />
    </button>
  );
}

function TrashIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 6h18" />
      <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <path d="M19 6 18 20a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
    </svg>
  );
}

function FireIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M8.5 14.5A2.5 2.5 0 0 0 11 17c1.4 0 2.5-1.1 2.5-2.5 0-2.5-3-3.5-3-6 0-1.5 1-3 2.5-4.5C9 6 7 8.5 7 12c0 1.5.5 2 1.5 2.5z" />
      <path d="M14 8c0 4 4 4 4 7a4 4 0 0 1-8 0c0-1 .5-2 1-3" />
    </svg>
  );
}
