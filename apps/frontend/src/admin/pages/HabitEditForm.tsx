import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { adminHabitsApi, type AdminHabit } from "../api";
import {
  CheckboxGroup,
  CustomSelect,
  FieldRow,
  TextArea,
  TextInput,
} from "../components/Form";
import { useAvailableChats, useUploadPhoto } from "../hooks";

type ProofType = "video_note" | "photo" | "text";

const PROOF_OPTIONS = [
  { value: "video_note", label: "Кружочек" },
  { value: "photo", label: "Фото" },
  { value: "text", label: "Текст" },
];

const TIMEZONE_OPTIONS = [
  "Europe/Moscow",
  "Europe/Berlin",
  "Europe/London",
  "Europe/Kiev",
  "Europe/Minsk",
  "Asia/Yekaterinburg",
  "Asia/Novosibirsk",
  "Asia/Krasnoyarsk",
  "Asia/Irkutsk",
  "Asia/Yakutsk",
  "Asia/Vladivostok",
  "Asia/Magadan",
  "Asia/Kamchatka",
  "Asia/Almaty",
  "Asia/Tashkent",
  "Asia/Tbilisi",
  "Asia/Yerevan",
  "Asia/Baku",
  "Asia/Dubai",
  "Asia/Tehran",
  "UTC",
];

interface FormState {
  title: string;
  description: string;
  photo_url: string;
  telegram_invite_link: string;
  stat_name: string;
  stat_icon: string;
  checkin_window_start: string;
  checkin_window_end: string;
  timezone: string;
  proof_types: ProofType[];
  price_month_rub: string;
  penalty_amount_rub: string;
  catcher_amount_rub: string;
  stat_gain_per_checkin: string;
  stat_loss_per_miss: string;
  member_limit: string;
  checkin_topic_link: string;
  notifications_topic_link: string;
  chat_topic_link: string;
}

const EMPTY: FormState = {
  title: "",
  description: "",
  photo_url: "",
  telegram_invite_link: "",
  stat_name: "Дисциплина",
  stat_icon: "🔥",
  checkin_window_start: "09:00",
  checkin_window_end: "21:00",
  timezone: "Europe/Moscow",
  proof_types: ["video_note"],
  price_month_rub: "299",
  penalty_amount_rub: "100",
  catcher_amount_rub: "0",
  stat_gain_per_checkin: "2",
  stat_loss_per_miss: "1",
  member_limit: "",
  checkin_topic_link: "",
  notifications_topic_link: "",
  chat_topic_link: "",
};

const toIntOrNull = (raw: string): number | null => {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? Math.trunc(n) : null;
};

const rubToKopecks = (raw: string): number => {
  const trimmed = raw.trim().replace(",", ".");
  const asNumber = Number(trimmed);
  if (!Number.isFinite(asNumber)) return 0;
  return Math.round(asNumber * 100);
};

const kopToRubStr = (kop: number): string =>
  Number.isFinite(kop) ? String(kop / 100) : "";

function habitToForm(h: AdminHabit): FormState {
  return {
    title: h.title,
    description: h.description ?? "",
    photo_url: h.photo_url ?? "",
    telegram_invite_link: h.telegram_invite_link ?? "",
    stat_name: h.stat_name,
    stat_icon: h.stat_icon ?? "",
    checkin_window_start: h.checkin_window_start.slice(0, 5),
    checkin_window_end: h.checkin_window_end.slice(0, 5),
    timezone: h.timezone,
    proof_types: (h.proof_types && h.proof_types.length > 0
      ? h.proof_types
      : [h.proof_type]) as ProofType[],
    price_month_rub: kopToRubStr(h.price_month),
    penalty_amount_rub: kopToRubStr(h.penalty_amount),
    catcher_amount_rub: kopToRubStr(h.catcher_amount_kopecks),
    stat_gain_per_checkin: String(h.stat_gain_per_checkin),
    stat_loss_per_miss: String(h.stat_loss_per_miss),
    member_limit: h.member_limit === null ? "" : String(h.member_limit),
    checkin_topic_link: h.checkin_topic_link ?? "",
    notifications_topic_link: h.notifications_topic_link ?? "",
    chat_topic_link: h.chat_topic_link ?? "",
  };
}

interface FormProps {
  habit: AdminHabit | null;
  loading: boolean;
  error: string | null;
}

export function HabitEditForm({ habit, loading, error }: FormProps) {
  const navigate = useNavigate();
  const [form, setForm] = useState<FormState>(EMPTY);
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [saveError, setSaveError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [currentChatId, setCurrentChatId] = useState<number>(habit?.chat_id ?? 0);
  const [currentChatTitle, setCurrentChatTitle] = useState<string>(habit?.title ?? "");
  const uploadPhoto = useUploadPhoto();
  const availableChatsQuery = useAvailableChats();

  useEffect(() => {
    if (habit) {
      setForm(habitToForm(habit));
      setTouched({});
      setCurrentChatId(habit.chat_id);
      const fromList = availableChatsQuery.data?.items.find(
        (c) => c.chat_id === habit.chat_id,
      );
      setCurrentChatTitle(fromList?.chat_title ?? habit.title);
    }
  }, [habit]);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12 text-muted">
        Загружаю...
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center gap-3 py-12 text-center">
        <div className="text-3xl" aria-hidden="true">
          ⚠️
        </div>
        <p className="text-sm text-danger">{error}</p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="min-h-[44px] rounded-card bg-primary px-5 py-3 text-sm font-semibold text-white"
        >
          Повторить
        </button>
      </div>
    );
  }

  if (!habit) return null;

  const touchedFields = (key: keyof FormState) => {
    setTouched((prev) => (prev[key] ? prev : { ...prev, [key]: true }));
  };

  const validate = (
    state: FormState,
  ): Partial<Record<keyof FormState, string>> => {
    const errors: Partial<Record<keyof FormState, string>> = {};
    if (state.title.trim().length < 3 || state.title.trim().length > 128) {
      errors.title = "От 3 до 128 символов";
    }
    if (!state.stat_name.trim()) {
      errors.stat_name = "Обязательно";
    }
    if (!/^\d{2}:\d{2}$/.test(state.checkin_window_start)) {
      errors.checkin_window_start = "HH:MM";
    }
    if (!/^\d{2}:\d{2}$/.test(state.checkin_window_end)) {
      errors.checkin_window_end = "HH:MM";
    }
    if (
      /^\d{2}:\d{2}$/.test(state.checkin_window_start) &&
      /^\d{2}:\d{2}$/.test(state.checkin_window_end) &&
      state.checkin_window_start >= state.checkin_window_end
    ) {
      errors.checkin_window_end = "Конец должен быть позже начала";
    }
    if (state.member_limit.trim()) {
      const ml = Number(state.member_limit);
      if (!Number.isFinite(ml) || ml <= 0 || !Number.isInteger(ml)) {
        errors.member_limit = "Целое > 0";
      }
    }
    const topicLinkRe = /^https?:\/\/t\.me\/c\/-?\d+\/\d+\/?$/;
    if (
      state.checkin_topic_link.trim() &&
      !topicLinkRe.test(state.checkin_topic_link.trim())
    ) {
      errors.checkin_topic_link = "Формат https://t.me/c/<chat_id>/<thread_id>";
    }
    if (
      state.notifications_topic_link.trim() &&
      !topicLinkRe.test(state.notifications_topic_link.trim())
    ) {
      errors.notifications_topic_link =
        "Формат https://t.me/c/<chat_id>/<thread_id>";
    }
    if (
      state.checkin_topic_link.trim() &&
      state.notifications_topic_link.trim() &&
      state.checkin_topic_link.trim() ===
        state.notifications_topic_link.trim()
    ) {
      errors.notifications_topic_link =
        "Топик уведомлений должен отличаться от топика чек-инов";
    }
    const chatLinkRe = /^https?:\/\/t\.me\/c\/-?\d+\/\d+\/?$/;
    if (
      state.chat_topic_link.trim() &&
      !chatLinkRe.test(state.chat_topic_link.trim())
    ) {
      errors.chat_topic_link = "Формат https://t.me/c/<chat_id>/<thread_id>";
    }
    return errors;
  };

  const errors = validate(form);
  const hasErrors = Object.keys(errors).length > 0;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaveError(null);

    const allTouched: Record<string, boolean> = {};
    (Object.keys(form) as (keyof FormState)[]).forEach(
      (k) => (allTouched[k] = true),
    );
    setTouched(allTouched);

    if (Object.keys(errors).length > 0) return;

    setSubmitting(true);
    try {
      await adminHabitsApi.update(habit.id, {
        title: form.title.trim(),
        description: form.description.trim() || null,
        photo_url: form.photo_url.trim() || null,
        telegram_invite_link: form.telegram_invite_link.trim() || null,
        stat_name: form.stat_name.trim(),
        stat_icon: form.stat_icon.trim() || null,
        checkin_window_start: form.checkin_window_start,
        checkin_window_end: form.checkin_window_end,
        timezone: form.timezone,
        proof_types: form.proof_types,
        price_month: rubToKopecks(form.price_month_rub),
        penalty_amount: rubToKopecks(form.penalty_amount_rub),
        catcher_amount_kopecks: rubToKopecks(form.catcher_amount_rub),
        stat_gain_per_checkin: toIntOrNull(form.stat_gain_per_checkin) ?? 2,
        stat_loss_per_miss: toIntOrNull(form.stat_loss_per_miss) ?? 1,
        member_limit: toIntOrNull(form.member_limit),
        chat_id: currentChatId,
        checkin_topic_link: form.checkin_topic_link.trim() || undefined,
        notifications_topic_link:
          form.notifications_topic_link.trim() || undefined,
        chat_topic_link: form.chat_topic_link.trim() || undefined,
      });
      navigate("/habits");
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const inputCls = "block w-full min-h-[44px] rounded-card border border-white/10 bg-surface px-3 py-2 text-sm text-text placeholder:text-muted/60 focus:border-primary focus:outline-none";

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <FieldRow label="Название клуба" error={touched.title ? errors.title : undefined}>
        <TextInput
          value={form.title}
          onChange={(e) => set("title", e.target.value)}
          onBlur={() => touchedFields("title")}
          maxLength={128}
        />
      </FieldRow>

      <FieldRow label="Описание">
        <TextArea
          value={form.description}
          onChange={(e) => set("description", e.target.value)}
          rows={3}
        />
      </FieldRow>

      <FieldRow
        label="Telegram invite-ссылка"
        error={
          touched.telegram_invite_link
            ? errors.telegram_invite_link
            : undefined
        }
      >
        <TextInput
          value={form.telegram_invite_link}
          onChange={(e) => set("telegram_invite_link", e.target.value)}
          onBlur={() => touchedFields("telegram_invite_link")}
          placeholder="https://t.me/+abc123"
          inputMode="url"
        />
        <p className="mt-1 text-xs text-muted">
          Ссылка-приглашение в группу клуба. Используется в кнопке
          «Присоединиться к клубу».
        </p>
      </FieldRow>

      <FieldRow label="Фото клуба">
        <div className="flex flex-col gap-3">
          {form.photo_url ? (
            <label
              className="group relative flex cursor-pointer items-center justify-center rounded-card border border-white/10 bg-canvas/60 p-2 transition hover:border-white/30"
              aria-label="Нажмите, чтобы заменить фото"
            >
              <img
                src={form.photo_url}
                alt="Превью"
                className="block max-h-80 w-full object-contain"
                loading="lazy"
              />
              <span className="pointer-events-none absolute inset-0 flex items-center justify-center rounded-card bg-black/0 text-sm font-medium text-white opacity-0 transition group-hover:bg-black/40 group-hover:opacity-100">
                {uploadPhoto.isPending ? "Загружаю..." : "Заменить фото"}
              </span>
              <input
                type="file"
                accept="image/jpeg,image/png,image/gif,image/webp"
                className="sr-only"
                disabled={uploadPhoto.isPending}
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  try {
                    const res = await uploadPhoto.mutateAsync(file);
                    set("photo_url", res.url);
                  } catch (err) {
                    // eslint-disable-next-line no-alert
                    alert(err instanceof Error ? err.message : String(err));
                  } finally {
                    e.target.value = "";
                  }
                }}
              />
            </label>
          ) : (
            <label
              className={`flex min-h-[120px] cursor-pointer flex-col items-center justify-center gap-2 rounded-card border border-dashed transition ${
                uploadPhoto.isPending
                  ? "border-primary/40 bg-primary/5"
                  : "border-white/20 bg-surface hover:border-white/40"
              }`}
            >
              <span className="text-2xl" aria-hidden="true">🖼️</span>
              <span className="text-sm font-medium text-text">
                {uploadPhoto.isPending ? "Загружаю..." : "Загрузить фото"}
              </span>
              <span className="text-xs text-muted">
                JPEG, PNG, GIF или WebP, до 5 MB
              </span>
              <input
                type="file"
                accept="image/jpeg,image/png,image/gif,image/webp"
                className="sr-only"
                disabled={uploadPhoto.isPending}
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  try {
                    const res = await uploadPhoto.mutateAsync(file);
                    set("photo_url", res.url);
                  } catch (err) {
                    // eslint-disable-next-line no-alert
                    alert(err instanceof Error ? err.message : String(err));
                  } finally {
                    e.target.value = "";
                  }
                }}
              />
            </label>
          )}
          {uploadPhoto.isError && (
            <p className="text-xs text-danger" role="alert">
              {uploadPhoto.error instanceof Error
                ? uploadPhoto.error.message
                : "Ошибка загрузки"}
            </p>
          )}
          {form.photo_url && (
            <button
              type="button"
              onClick={() => set("photo_url", "")}
              className="self-start min-h-[36px] rounded-card border border-white/10 bg-surface px-3 py-1.5 text-xs font-medium text-muted transition hover:border-danger/40 hover:text-danger"
            >
              Удалить фото
            </button>
          )}
        </div>
      </FieldRow>

      <FieldRow label="Chat ID (Telegram)">
        <div className="flex items-center gap-2">
          <div
            className={`flex-1 min-h-[52px] flex flex-col justify-center rounded-card border px-3 py-2 ${
              currentChatId !== 0
                ? "border-emerald-500/40 bg-emerald-500/5"
                : "border-white/10 bg-surface"
            }`}
          >
            {currentChatId !== 0 ? (
              <>
                <span className="text-sm font-semibold text-text leading-tight">
                  {currentChatTitle || "Без названия"}
                </span>
                <span className="text-xs font-mono text-muted leading-tight mt-0.5">
                  chat_id: {currentChatId}
                </span>
              </>
            ) : (
              <span className="text-sm text-muted">
                не привязан — выбери группу ниже
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={async () => {
              await availableChatsQuery.refetch();
              if (currentChatId !== 0) {
                const fromList = availableChatsQuery.data?.items.find(
                  (c) => c.chat_id === currentChatId,
                );
                if (fromList?.chat_title) {
                  setCurrentChatTitle(fromList.chat_title);
                }
              }
            }}
            disabled={availableChatsQuery.isFetching}
            className="min-h-[44px] shrink-0 rounded-card border border-white/10 bg-surface px-4 py-2 text-sm font-medium text-text transition hover:border-white/20 disabled:opacity-50"
          >
            {availableChatsQuery.isFetching ? "Обновляю..." : "Обновить"}
          </button>
        </div>

        <p className="mt-2 text-xs text-muted">
          Можешь выбрать другую группу Telegram, куда добавлен бот.
        </p>

        {availableChatsQuery.isLoading && (
          <p className="mt-2 text-xs text-muted">Загружаю список…</p>
        )}

        {availableChatsQuery.data && availableChatsQuery.data.items.length === 0 && (
          <div className="mt-2 rounded-card border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-200">
            Бот пока не добавлен ни в одну группу. Добавь
            @join_prideclub_bot в группу и нажми «Обновить».
          </div>
        )}

        {availableChatsQuery.data && availableChatsQuery.data.items.length > 0 && (
          <ul className="mt-2 flex flex-col gap-1" role="listbox">
            {availableChatsQuery.data.items.map((chat) => {
              const selected = currentChatId === chat.chat_id;
              const disabled =
                chat.bound_to_habit_id !== null &&
                chat.bound_to_habit_id !== habit.id;
              return (
                <li key={chat.chat_id}>
                  <button
                    type="button"
                    onClick={() => {
                      if (disabled) return;
                      setCurrentChatId(chat.chat_id);
                      setCurrentChatTitle(chat.chat_title ?? "");
                            }}
                    disabled={disabled}
                    aria-pressed={selected}
                    className={`w-full min-h-[52px] flex items-center gap-3 rounded-card border px-3 py-2 text-left transition ${
                      selected
                        ? "border-emerald-500/60 bg-emerald-500/10"
                        : disabled
                          ? "border-white/5 bg-surface/40 cursor-not-allowed"
                          : "border-white/10 bg-surface hover:border-white/30"
                    }`}
                  >
                    <span className="flex-1 flex flex-col">
                      <span className="text-sm font-semibold text-text leading-tight">
                        {chat.chat_title ?? "Без названия"}
                      </span>
                      <span className="text-xs font-mono text-muted leading-tight mt-0.5">
                        {chat.chat_type ?? "?"} · {chat.chat_id}
                      </span>
                    </span>
                    <span className="text-xs shrink-0">
                      {disabled
                        ? `уже у «${chat.bound_to_habit_title ?? "?"}»`
                        : selected
                          ? "✓ выбран"
                          : "выбрать"}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </FieldRow>

      <FieldRow
        label="Ссылка на топик чек-инов"
        error={touched.checkin_topic_link ? errors.checkin_topic_link : undefined}
      >
        <TextInput
          value={form.checkin_topic_link}
          onChange={(e) => set("checkin_topic_link", e.target.value)}
          onBlur={() => touchedFields("checkin_topic_link")}
          placeholder="Вставь ссылку на топик"
          inputMode="url"
        />
      </FieldRow>

      <FieldRow
        label="Ссылка на топик уведомлений"
        error={
          touched.notifications_topic_link
            ? errors.notifications_topic_link
            : undefined
        }
      >
        <TextInput
          value={form.notifications_topic_link}
          onChange={(e) => set("notifications_topic_link", e.target.value)}
          onBlur={() => touchedFields("notifications_topic_link")}
          placeholder="Вставь ссылку на топик"
          inputMode="url"
        />
      </FieldRow>

      <FieldRow
        label="Ссылка на топик чата"
        error={touched.chat_topic_link ? errors.chat_topic_link : undefined}
      >
        <TextInput
          value={form.chat_topic_link}
          onChange={(e) => set("chat_topic_link", e.target.value)}
          onBlur={() => touchedFields("chat_topic_link")}
          placeholder="Вставь ссылку на топик"
          inputMode="url"
        />
      </FieldRow>

      <FieldRow label="Характеристика">
        <div className="grid grid-cols-2 gap-2">
          <TextInput
            value={form.stat_name}
            onChange={(e) => set("stat_name", e.target.value)}
            onBlur={() => touchedFields("stat_name")}
            maxLength={64}
          />
          <TextInput
            value={form.stat_icon}
            onChange={(e) => set("stat_icon", e.target.value)}
            maxLength={16}
          />
        </div>
        {touched.stat_name && errors.stat_name && (
          <p className="mt-1 text-xs text-danger" role="alert">
            {errors.stat_name}
          </p>
        )}
      </FieldRow>

      <FieldRow label="Окно чекина">
        <div className="grid grid-cols-2 gap-2">
          <TextInput
            type="time"
            value={form.checkin_window_start}
            onChange={(e) => {
              set("checkin_window_start", e.target.value);
              touchedFields("checkin_window_start");
            }}
          />
          <TextInput
            type="time"
            value={form.checkin_window_end}
            onChange={(e) => {
              set("checkin_window_end", e.target.value);
              touchedFields("checkin_window_end");
            }}
          />
        </div>
        {(touched.checkin_window_start || touched.checkin_window_end) &&
          (errors.checkin_window_start || errors.checkin_window_end) && (
            <p className="mt-1 text-xs text-danger" role="alert">
              {errors.checkin_window_start || errors.checkin_window_end}
            </p>
          )}
      </FieldRow>

      <FieldRow label="Часовой пояс">
        <CustomSelect
          value={form.timezone}
          onChange={(next) => set("timezone", next)}
          options={TIMEZONE_OPTIONS.map((tz) => ({ value: tz, label: tz }))}
          ariaLabel="Часовой пояс клуба"
        />
      </FieldRow>

      <FieldRow label="Типы подтверждения (можно выбрать несколько)">
        <CheckboxGroup
          name="proof_types"
          options={PROOF_OPTIONS}
          value={form.proof_types}
          onChange={(next) => set("proof_types", next as ProofType[])}
        />
      </FieldRow>

      <FieldRow label="Цена в месяц (₽)">
        <TextInput
          value={form.price_month_rub}
          onChange={(e) => set("price_month_rub", e.target.value)}
          inputMode="decimal"
        />
        <p className="mt-1 text-xs text-muted">
          Изменения применяются к новым подпискам. Уже оплаченные
          участники продолжают действовать до конца оплаченного периода
          по старой цене.
        </p>
      </FieldRow>

      <FieldRow label="Штраф за пропуск (₽)">
        <TextInput
          value={form.penalty_amount_rub}
          onChange={(e) => set("penalty_amount_rub", e.target.value)}
          inputMode="decimal"
        />
        <p className="mt-1 text-xs text-muted">
          Применяется к будущим штрафам. Прошлые штрафы не пересчитываются.
        </p>
      </FieldRow>

      {/* Pravki-catcher-deposit (Phase 1 Task 1.6, 2026-08-21): сумма ловцу
          от штрафа. Можно менять БЕЗ заморозки после первого участника —
          не финансовое обязательство, только правило для будущих поимок.
          Уже оплаченные транзакции не затрагиваются (catcher_amount
          кэшируется в Penalty на момент поимки). */}
      <FieldRow label="Сумма ловцу (₽)">
        <TextInput
          value={form.catcher_amount_rub}
          onChange={(e) => set("catcher_amount_rub", e.target.value)}
          inputMode="decimal"
        />
        <p className="mt-1 text-xs text-muted">
          0 = всё идёт в призовой фонд клуба. Если сумма ≥ штрафа —
          всё уходит ловцу (clamp на бэкенде).
        </p>
      </FieldRow>

      <FieldRow label="Прирост / убыль за чек-ин">
        <div className="grid grid-cols-2 gap-2">
          <TextInput
            type="number"
            min={1}
            value={form.stat_gain_per_checkin}
            onChange={(e) => set("stat_gain_per_checkin", e.target.value)}
          />
          <TextInput
            type="number"
            min={1}
            value={form.stat_loss_per_miss}
            onChange={(e) => set("stat_loss_per_miss", e.target.value)}
          />
        </div>
      </FieldRow>

      <FieldRow label="Лимит участников" error={touched.member_limit ? errors.member_limit : undefined}>
        <TextInput
          type="number"
          min={1}
          value={form.member_limit}
          onChange={(e) => set("member_limit", e.target.value)}
          onBlur={() => touchedFields("member_limit")}
          inputMode="numeric"
        />
      </FieldRow>

      {saveError && (
        <div className="rounded-card border border-danger/30 bg-danger/10 p-3 text-sm text-danger" role="alert">
          {saveError}
        </div>
      )}

      <div className="flex gap-2 pb-4 pt-2">
        <button
          type="submit"
          disabled={submitting || hasErrors}
          aria-busy={submitting}
          className="flex-1 min-h-[44px] rounded-card bg-primary px-5 py-3 text-sm font-semibold text-white transition hover:opacity-90 disabled:opacity-50"
        >
          {submitting ? "Сохраняю..." : "Сохранить"}
        </button>
        <button
          type="button"
          onClick={() => navigate("/habits")}
          className="min-h-[44px] rounded-card border border-white/10 bg-surface px-5 py-3 text-sm font-medium text-text transition hover:border-white/20"
        >
          Отмена
        </button>
      </div>

      <input className={inputCls} type="hidden" value="" aria-hidden="true" readOnly />
    </form>
  );
}
