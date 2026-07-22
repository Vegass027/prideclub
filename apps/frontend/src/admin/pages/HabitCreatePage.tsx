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
import { useCreateHabit } from "../hooks";

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

const TR_TELEGRAM_RE = /^https:\/\/t\.me\/(\+|[A-Za-z0-9_]+)/;

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
  proof_type: ProofType;
  price_month_rub: string;
  penalty_amount_rub: string;
  stat_gain_per_checkin: string;
  stat_loss_per_miss: string;
  member_limit: string;
}

const INITIAL_STATE: FormState = {
  title: "",
  description: "",
  photo_url: "",
  telegram_invite_link: "",
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
}

const errorsOf = (state: RawForm): Partial<Record<keyof FormState, string>> => {
  const errors: Partial<Record<keyof FormState, string>> = {};

  if (state.title.trim().length < 3 || state.title.trim().length > 128) {
    errors.title = "От 3 до 128 символов";
  }
  if (!state.description.trim()) {
    errors.description = "Обязательно";
  }
  if (!state.photo_url.trim()) {
    errors.photo_url = "Обязательно";
  }
  if (!state.telegram_invite_link.trim()) {
    errors.telegram_invite_link = "Обязательно";
  } else if (!TR_TELEGRAM_RE.test(state.telegram_invite_link.trim())) {
    errors.telegram_invite_link = "Ссылка должна быть https://t.me/...";
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

  const errors = useMemo(() => errorsOf(state), [state]);
  const errorList = Object.values(errors).filter(Boolean) as string[];

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setState((prev) => ({ ...prev, [key]: value }));
  };

  const markTouched = (field: string) => {
    setTouched((prev) => (prev[field] ? prev : { ...prev, [field]: true }));
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
        chat_id: 0,
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

        <FieldRow
          label="Фото клуба"
          error={touched.photo_url ? errors.photo_url : undefined}
        >
          <TextInput
            type="url"
            value={state.photo_url}
            onChange={(e) => set("photo_url", e.target.value)}
            onBlur={() => markTouched("photo_url")}
            inputMode="url"
          />
        </FieldRow>

        <FieldRow
          label="Telegram-инвайт клуба"
          error={
            touched.telegram_invite_link ? errors.telegram_invite_link : undefined
          }
        >
          <TextInput
            type="url"
            value={state.telegram_invite_link}
            onChange={(e) => set("telegram_invite_link", e.target.value)}
            onBlur={() => markTouched("telegram_invite_link")}
            inputMode="url"
          />
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
