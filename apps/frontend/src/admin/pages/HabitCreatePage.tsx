import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { EmptyState } from "@/shared/ui/EmptyState";
import { ScreenLayout } from "@/shared/ui/ScreenLayout";
import { PageHeader } from "@/shared/ui/PageHeader";
import {
  CustomSelect,
  FieldRow,
  RadioGroup,
  TextArea,
  TextInput,
} from "../components/Form";
import { useAvailableChats, useCreateHabit, useRefreshChat, useUploadPhoto } from "../hooks";

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
  chat_id: number;
  chat_title: string;
  stat_name: string;
  stat_icon: string;
  checkin_window_start: string;
  checkin_window_end: string;
  timezone: string;
  proof_type: ProofType;
  price_month_rub: string;
  penalty_amount_rub: string;
  stat_gain_per_checkin: string;
  stat_loss_per_miss: string;
  member_limit: string;
  checkin_topic_link: string;
  notifications_topic_link: string;
}

const INITIAL_STATE: FormState = {
  title: "",
  description: "",
  photo_url: "",
  telegram_invite_link: "",
  chat_id: 0,
  chat_title: "",
  stat_name: "Дисциплина",
  stat_icon: "🔥",
  checkin_window_start: "09:00",
  checkin_window_end: "21:00",
  timezone: "Europe/Moscow",
  proof_type: "video_note",
  price_month_rub: "299",
  penalty_amount_rub: "100",
  stat_gain_per_checkin: "2",
  stat_loss_per_miss: "1",
  member_limit: "",
  checkin_topic_link: "",
  notifications_topic_link: "",
};

const rubToKopecks = (rub: string): number => {
  const trimmed = rub.trim().replace(",", ".");
  const asNumber = Number(trimmed);
  if (!Number.isFinite(asNumber)) return 0;
  return Math.round(asNumber * 100);
};

const toIntOrNull = (raw: string): number | null => {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? Math.trunc(n) : null;
};

interface RawForm {
  title: string;
  description: string;
  photo_url: string;
  telegram_invite_link: string;
  chat_id: number;
  chat_title: string;
  stat_name: string;
  stat_icon: string;
  checkin_window_start: string;
  checkin_window_end: string;
  timezone: string;
  proof_type: ProofType;
  price_month_rub: string;
  penalty_amount_rub: string;
  stat_gain_per_checkin: string;
  stat_loss_per_miss: string;
  member_limit: string;
  checkin_topic_link: string;
  notifications_topic_link: string;
}

const errorsOf = (state: RawForm): Partial<Record<keyof FormState, string>> => {
  const errors: Partial<Record<keyof FormState, string>> = {};

  if (state.title.trim().length < 3 || state.title.trim().length > 128) {
    errors.title = "От 3 до 128 символов";
  }
  if (!state.description.trim()) {
    errors.description = "Обязательно";
  }
  if (state.chat_id === 0) {
    errors.chat_id = "Выбери группу Telegram, куда добавлен бот";
  }
  if (!state.stat_name.trim()) {
    errors.stat_name = "Обязательно";
  }
  const topicLinkRe = /^https?:\/\/t\.me\/c\/-?\d+\/\d+\/?$/;
  if (!topicLinkRe.test(state.checkin_topic_link.trim())) {
    errors.checkin_topic_link =
      "Формат https://t.me/c/<chat_id>/<thread_id>";
  }
  if (!topicLinkRe.test(state.notifications_topic_link.trim())) {
    errors.notifications_topic_link =
      "Формат https://t.me/c/<chat_id>/<thread_id>";
  }
  if (
    state.checkin_topic_link.trim() &&
    state.notifications_topic_link.trim() &&
    state.checkin_topic_link.trim() === state.notifications_topic_link.trim()
  ) {
    errors.notifications_topic_link =
      "Топик уведомлений должен отличаться от топика чек-инов";
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

  const priceRub = Number(state.price_month_rub.replace(",", "."));
  if (!Number.isFinite(priceRub) || priceRub <= 0) errors.price_month_rub = "> 0 ₽";
  const penaltyRub = Number(state.penalty_amount_rub.replace(",", "."));
  if (!Number.isFinite(penaltyRub) || penaltyRub <= 0) errors.penalty_amount_rub = "> 0 ₽";

  if (state.member_limit.trim()) {
    const ml = Number(state.member_limit);
    if (!Number.isFinite(ml) || ml <= 0 || !Number.isInteger(ml)) {
      errors.member_limit = "Целое > 0";
    }
  }

  return errors;
};

export function HabitCreatePage() {
  const navigate = useNavigate();
  const [state, setState] = useState<FormState>(INITIAL_STATE);
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const create = useCreateHabit();
  const availableChatsQuery = useAvailableChats();
  const refreshChat = useRefreshChat();
  const uploadPhoto = useUploadPhoto();

  const errors = useMemo(() => errorsOf(state), [state]);
  const errorList = Object.values(errors).filter(Boolean) as string[];

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setState((prev) => ({ ...prev, [key]: value }));
  };

  const markTouched = (field: string) => {
    setTouched((prev) => (prev[field] ? prev : { ...prev, [field]: true }));
  };

  const handlePickChat = (chatId: number, title: string | null) => {
    setState((prev) => ({
      ...prev,
      chat_id: chatId,
      chat_title: title ?? "",
    }));
  };

  const handleRefreshAll = async () => {
    await availableChatsQuery.refetch();
    if (state.chat_id !== 0) {
      try {
        const res = await refreshChat.mutateAsync(state.chat_id);
        if (res.ok) {
          setState((prev) => ({
            ...prev,
            chat_title: res.chat_title ?? prev.chat_title,
          }));
        }
      } catch {
        // ignore — главное обновили список
      }
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const allTouched: Record<string, boolean> = {};
    (Object.keys(state) as (keyof FormState)[]).forEach(
      (k) => (allTouched[k] = true),
    );
    setTouched(allTouched);

    if (errorList.length > 0) return;
    const priceKop = rubToKopecks(state.price_month_rub);
    const penaltyKop = rubToKopecks(state.penalty_amount_rub);
    if (priceKop <= 0 || penaltyKop <= 0) return;

    create.mutate(
      {
        title: state.title.trim(),
        description: state.description.trim() || null,
        photo_url: state.photo_url.trim() || null,
        telegram_invite_link: state.telegram_invite_link.trim() || null,
        stat_name: state.stat_name.trim(),
        stat_icon: state.stat_icon.trim() || null,
        chat_id: state.chat_id,
        checkin_window_start: state.checkin_window_start,
        checkin_window_end: state.checkin_window_end,
        timezone: state.timezone,
        proof_type: state.proof_type,
        price_month: priceKop,
        penalty_amount: penaltyKop,
        stat_gain_per_checkin: toIntOrNull(state.stat_gain_per_checkin) ?? 2,
        stat_loss_per_miss: toIntOrNull(state.stat_loss_per_miss) ?? 1,
        member_limit: toIntOrNull(state.member_limit),
        curator_id: null,
        checkin_topic_link: state.checkin_topic_link.trim(),
        notifications_topic_link: state.notifications_topic_link.trim(),
      },
      {
        onSuccess: () => navigate("/habits"),
      },
    );
  };

  const serverError =
    create.isError && create.error ? String(create.error) : null;

  return (
    <ScreenLayout>
      <PageHeader title="Новый клуб" back backTo="/habits" />

      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <FieldRow label="Название клуба" error={touched.title ? errors.title : undefined}>
          <TextInput
            value={state.title}
            onChange={(e) => set("title", e.target.value)}
            onBlur={() => markTouched("title")}
            maxLength={128}
          />
        </FieldRow>

        <FieldRow
          label="Описание"
          error={touched.description ? errors.description : undefined}
        >
          <TextArea
            value={state.description}
            onChange={(e) => set("description", e.target.value)}
            onBlur={() => markTouched("description")}
            rows={3}
          />
        </FieldRow>

        <FieldRow label="Фото клуба">
          <div className="flex flex-col gap-3">
            {state.photo_url ? (
              <div className="relative w-full overflow-hidden rounded-card border border-white/10 bg-surface">
                {/* eslint-disable-next-line jsx-a11y/img-redundant-alt */}
                <img
                  src={state.photo_url}
                  alt="Превью фото клуба"
                  className="block w-full max-h-64 object-cover"
                />
                <button
                  type="button"
                  onClick={() => set("photo_url", "")}
                  className="absolute right-2 top-2 min-h-[32px] rounded-card bg-black/60 px-3 py-1 text-xs font-medium text-white transition hover:bg-black/80"
                  aria-label="Удалить фото"
                >
                  Удалить
                </button>
              </div>
            ) : (
              <label
                className={`flex min-h-[120px] cursor-pointer flex-col items-center justify-center gap-2 rounded-card border border-dashed transition ${
                  uploadPhoto.isPending
                    ? "border-primary/40 bg-primary/5"
                    : "border-white/20 bg-surface hover:border-white/40"
                }`}
              >
                <span className="text-2xl" aria-hidden="true">
                  🖼️
                </span>
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
          </div>
        </FieldRow>

        <FieldRow label="Chat ID (Telegram)">
          <div className="flex items-center gap-2">
            <div
              className={`flex-1 min-h-[52px] flex flex-col justify-center rounded-card border px-3 py-2 ${
                state.chat_id !== 0
                  ? "border-emerald-500/40 bg-emerald-500/5"
                  : "border-white/10 bg-surface"
              }`}
            >
              {state.chat_id !== 0 ? (
                <>
                  <span className="text-sm font-semibold text-text leading-tight">
                    {state.chat_title || "Без названия"}
                  </span>
                  <span className="text-xs font-mono text-muted leading-tight mt-0.5">
                    chat_id: {state.chat_id}
                  </span>
                </>
              ) : (
                <span className="text-sm text-muted">
                  не выбран — выбери группу ниже
                </span>
              )}
            </div>
            <button
              type="button"
              onClick={handleRefreshAll}
              disabled={
                availableChatsQuery.isFetching || refreshChat.isPending
              }
              aria-busy={availableChatsQuery.isFetching || refreshChat.isPending}
              className="min-h-[44px] shrink-0 rounded-card border border-white/10 bg-surface px-4 py-2 text-sm font-medium text-text transition hover:border-white/20 disabled:opacity-50"
            >
              {availableChatsQuery.isFetching || refreshChat.isPending
                ? "Обновляю..."
                : "Обновить"}
            </button>
          </div>

          <p className="mt-2 text-xs text-muted">
            Добавь бота{" "}
            <span className="text-text">@join_prideclub_bot</span> в
            Telegram-группу клуба и выбери её ниже. Если переименовал группу —
            нажми «Обновить».
          </p>

          {availableChatsQuery.isLoading && (
            <p className="mt-2 text-xs text-muted">Загружаю список…</p>
          )}

          {availableChatsQuery.data && availableChatsQuery.data.items.length === 0 && (
            <div className="mt-2 rounded-card border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-200">
              Бот пока не добавлен ни в одну группу. Открой Telegram, добавь
              @join_prideclub_bot в группу клуба и нажми «Обновить».
            </div>
          )}

          {availableChatsQuery.data && availableChatsQuery.data.items.length > 0 && (
            <ul className="mt-2 flex flex-col gap-1" role="listbox" aria-label="Группы Telegram">
              {availableChatsQuery.data.items.map((chat) => {
                const selected = state.chat_id === chat.chat_id;
                const disabled = chat.bound_to_habit_id !== null;
                return (
                  <li key={chat.chat_id}>
                    <button
                      type="button"
                      onClick={() =>
                        !disabled && handlePickChat(chat.chat_id, chat.chat_title)
                      }
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
                          ? `уже привязан к «${chat.bound_to_habit_title ?? "?"}»`
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
          error={
            touched.checkin_topic_link ? errors.checkin_topic_link : undefined
          }
        >
          <TextInput
            value={state.checkin_topic_link}
            onChange={(e) => set("checkin_topic_link", e.target.value)}
            onBlur={() => markTouched("checkin_topic_link")}
            placeholder="https://t.me/c/<chat_id>/<thread_id>"
            inputMode="url"
            aria-invalid={Boolean(
              touched.checkin_topic_link && errors.checkin_topic_link,
            )}
          />
          <p className="mt-1 text-xs text-muted">
            Открой Telegram-группу как супергруппу с топиками, создай топик
            «Чек-ины», скопируй ссылку на сообщение в этом топике и вставь сюда.
          </p>
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
            value={state.notifications_topic_link}
            onChange={(e) => set("notifications_topic_link", e.target.value)}
            onBlur={() => markTouched("notifications_topic_link")}
            placeholder="https://t.me/c/<chat_id>/<thread_id>"
            inputMode="url"
            aria-invalid={Boolean(
              touched.notifications_topic_link &&
                errors.notifications_topic_link,
            )}
          />
          <p className="mt-1 text-xs text-muted">
            Сюда бот будет писать «{`👨🏽‍🦰 X словил(а) 👨🏽‍🦰 Y`}» и сообщения о
            штрафах за пропуск.
          </p>
        </FieldRow>

        <FieldRow label="Характеристика">
          <div className="grid grid-cols-2 gap-2">
            <TextInput
              value={state.stat_name}
              onChange={(e) => set("stat_name", e.target.value)}
              onBlur={() => markTouched("stat_name")}
              maxLength={64}
              aria-invalid={Boolean(touched.stat_name && errors.stat_name)}
            />
            <TextInput
              value={state.stat_icon}
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
              value={state.checkin_window_start}
              onChange={(e) => {
                set("checkin_window_start", e.target.value);
                markTouched("checkin_window_start");
              }}
            />
            <TextInput
              type="time"
              value={state.checkin_window_end}
              onChange={(e) => {
                set("checkin_window_end", e.target.value);
                markTouched("checkin_window_end");
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
            value={state.timezone}
            onChange={(next) => set("timezone", next)}
            options={TIMEZONE_OPTIONS.map((tz) => ({ value: tz, label: tz }))}
            ariaLabel="Часовой пояс клуба"
          />
        </FieldRow>

        <FieldRow label="Тип подтверждения">
          <RadioGroup
            name="proof_type"
            options={PROOF_OPTIONS}
            value={state.proof_type}
            onChange={(next) => set("proof_type", next as ProofType)}
          />
        </FieldRow>

        <FieldRow
          label="Цена в месяц (₽)"
          error={touched.price_month_rub ? errors.price_month_rub : undefined}
        >
          <TextInput
            value={state.price_month_rub}
            onChange={(e) => set("price_month_rub", e.target.value)}
            onBlur={() => markTouched("price_month_rub")}
            inputMode="decimal"
          />
        </FieldRow>

        <FieldRow
          label="Штраф за пропуск (₽)"
          error={
            touched.penalty_amount_rub ? errors.penalty_amount_rub : undefined
          }
        >
          <TextInput
            value={state.penalty_amount_rub}
            onChange={(e) => set("penalty_amount_rub", e.target.value)}
            onBlur={() => markTouched("penalty_amount_rub")}
            inputMode="decimal"
          />
        </FieldRow>

        <FieldRow label="Прирост / убыль за чек-ин">
          <div className="grid grid-cols-2 gap-2">
            <TextInput
              type="number"
              min={1}
              value={state.stat_gain_per_checkin}
              onChange={(e) => set("stat_gain_per_checkin", e.target.value)}
            />
            <TextInput
              type="number"
              min={1}
              value={state.stat_loss_per_miss}
              onChange={(e) => set("stat_loss_per_miss", e.target.value)}
            />
          </div>
        </FieldRow>

        <FieldRow
          label="Лимит участников"
          error={touched.member_limit ? errors.member_limit : undefined}
        >
          <TextInput
            type="number"
            min={1}
            value={state.member_limit}
            onChange={(e) => set("member_limit", e.target.value)}
            onBlur={() => markTouched("member_limit")}
            inputMode="numeric"
          />
        </FieldRow>

        {create.isError && serverError && (
          <EmptyState
            icon="⚠️"
            title="Не удалось создать клуб"
            description={serverError}
          />
        )}

        <div className="flex gap-2 pb-4 pt-2">
          <button
            type="submit"
            disabled={create.isPending || errorList.length > 0}
            aria-busy={create.isPending}
            className="flex-1 min-h-[44px] rounded-card bg-primary px-5 py-3 text-sm font-semibold text-white transition hover:opacity-90 disabled:opacity-50"
          >
            {create.isPending ? "Создаю..." : "Создать клуб"}
          </button>
          <button
            type="button"
            onClick={() => navigate("/habits")}
            className="min-h-[44px] rounded-card border border-white/10 bg-surface px-5 py-3 text-sm font-medium text-text transition hover:border-white/20"
          >
            Отмена
          </button>
        </div>
      </form>
    </ScreenLayout>
  );
}
