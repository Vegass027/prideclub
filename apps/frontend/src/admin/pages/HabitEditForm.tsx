import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { adminHabitsApi, type AdminHabit } from "../api";
import {
  CustomSelect,
  FieldRow,
  RadioGroup,
  TextArea,
  TextInput,
} from "../components/Form";

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
  proof_type: "video_note",
  price_month_rub: "299",
  penalty_amount_rub: "100",
  stat_gain_per_checkin: "2",
  stat_loss_per_miss: "1",
  member_limit: "",
};

const toIntOrNull = (raw: string): number | null => {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? Math.trunc(n) : null;
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
    proof_type: h.proof_type as ProofType,
    price_month_rub: kopToRubStr(h.price_month),
    penalty_amount_rub: kopToRubStr(h.penalty_amount),
    stat_gain_per_checkin: String(h.stat_gain_per_checkin),
    stat_loss_per_miss: String(h.stat_loss_per_miss),
    member_limit: h.member_limit === null ? "" : String(h.member_limit),
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

  useEffect(() => {
    if (habit) {
      setForm(habitToForm(habit));
      setTouched({});
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

  const financialsFrozen = habit.active_members_count > 0;
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
    if (!state.photo_url.trim()) {
      errors.photo_url = "Обязательно";
    }
    if (
      state.telegram_invite_link.trim() &&
      !TR_TELEGRAM_RE.test(state.telegram_invite_link.trim())
    ) {
      errors.telegram_invite_link = "Ссылка должна быть https://t.me/...";
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
        proof_type: form.proof_type,
        stat_gain_per_checkin: toIntOrNull(form.stat_gain_per_checkin) ?? 2,
        stat_loss_per_miss: toIntOrNull(form.stat_loss_per_miss) ?? 1,
        member_limit: toIntOrNull(form.member_limit),
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

      <FieldRow label="Фото клуба" error={touched.photo_url ? errors.photo_url : undefined}>
        <TextInput
          type="url"
          value={form.photo_url}
          onChange={(e) => set("photo_url", e.target.value)}
          onBlur={() => touchedFields("photo_url")}
        />
      </FieldRow>

      <FieldRow label="Telegram-инвайт клуба" error={touched.telegram_invite_link ? errors.telegram_invite_link : undefined}>
        <TextInput
          type="url"
          value={form.telegram_invite_link}
          onChange={(e) => set("telegram_invite_link", e.target.value)}
          onBlur={() => touchedFields("telegram_invite_link")}
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

      <FieldRow label="Тип подтверждения">
        <RadioGroup
          name="proof_type"
          options={PROOF_OPTIONS}
          value={form.proof_type}
          onChange={(next) => set("proof_type", next as ProofType)}
        />
      </FieldRow>

      {!financialsFrozen && (
        <>
          <FieldRow label="Цена в месяц (₽)">
            <TextInput
              value={form.price_month_rub}
              onChange={(e) => set("price_month_rub", e.target.value)}
              inputMode="decimal"
            />
          </FieldRow>

          <FieldRow label="Штраф за пропуск (₽)">
            <TextInput
              value={form.penalty_amount_rub}
              onChange={(e) => set("penalty_amount_rub", e.target.value)}
              inputMode="decimal"
            />
          </FieldRow>
        </>
      )}

      {financialsFrozen && (
        <div className="rounded-card border border-white/10 bg-surface/60 p-3 text-xs text-muted">
          Цена и штраф заморожены — в клубе уже {habit.active_members_count}{" "}
          участник{habit.active_members_count === 1 ? "" : "ов"}.
        </div>
      )}

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
