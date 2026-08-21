// @vitest-environment jsdom
// Pravki-catcher-deposit (Phase 1 Task 1.6b, 2026-08-21): визуальное
// отображение catcher_amount_kopecks в карточке клуба.
//
// Кейсы:
// (a) catcher_amount_kopecks=0 (default) → строка НЕ показывается
//     (старое поведение "всё в фонд" — незачем владельцу видеть неактивную
//     настройку, шум в карточке).
// (b) catcher_amount_kopecks > 0 И < penalty_amount → показывается
//     «Ловцу за поимку: X ₽» без warning.
// (c) catcher_amount_kopecks >= penalty_amount → показывается с warning
//     «⚠️ вся сумма штрафа уходит ловцу». Тултип объясняет clamp
//     простым человеческим текстом (без упоминаний apply_catch).
//
// Паттерн скопирован с MembersPage.test.tsx + HabitEditForm.test.tsx
// (jsdom + vi.hoisted + vi.mock).
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AdminHabitCard } from "../AdminHabitCard";
import type { AdminHabit } from "@/admin/api";

function makeHabit(overrides: Partial<AdminHabit> = {}): AdminHabit {
  return {
    id: "habit-1",
    title: "Клуб «Тест»",
    description: "Test desc",
    chat_id: 100,
    checkin_window_start: "09:00:00",
    checkin_window_end: "21:00:00",
    timezone: "Europe/Moscow",
    penalty_amount: 30000, // 300₽
    catcher_amount_kopecks: 0, // default
    price_month: 1000_00, // 1000₽
    proof_type: "video_note",
    proof_types: ["video_note"],
    prize_pool: 0,
    is_active: true,
    photo_url: null,
    telegram_invite_link: null,
    stat_name: "Выносливость",
    stat_icon: "🔥",
    stat_gain_per_checkin: 2,
    stat_loss_per_miss: 1,
    member_limit: null,
    curator_id: null,
    checkin_topic_thread_id: 1,
    notifications_topic_thread_id: 2,
    chat_topic_thread_id: null,
    checkin_topic_link: null,
    notifications_topic_link: null,
    chat_topic_link: null,
    archived_at: null,
    created_at: "2026-08-21T10:00:00Z",
    active_members_count: 2,
    ...overrides,
  };
}

const noopCallbacks = {
  onToggle: vi.fn(),
  onDelete: vi.fn(),
  onRestore: vi.fn(),
  onPermanentDelete: vi.fn(),
};

function renderCard(habit: AdminHabit) {
  return render(
    <MemoryRouter>
      <AdminHabitCard habit={habit} busy={false} {...noopCallbacks} />
    </MemoryRouter>,
  );
}

describe("AdminHabitCard — catcher_amount_kopecks (Pravki-catcher-deposit Task 1.6b)", () => {
  it("не показывает строку при catcher_amount_kopecks=0 (default)", () => {
    // default = всё в фонд, старая логика — скрываем чтобы не шуметь
    const habit = makeHabit({ catcher_amount_kopecks: 0 });
    renderCard(habit);

    expect(screen.queryByText("Ловцу за поимку:")).toBeNull();
  });

  it("показывает 'Ловцу за поимку: 200 ₽' при catcher_amount_kopecks=20000 (200₽), без warning", () => {
    // penalty=300₽, catcher=200₽ → меньше штрафа, без warning
    const habit = makeHabit({
      penalty_amount: 30000,
      catcher_amount_kopecks: 20000,
    });
    renderCard(habit);

    // formatRub делит на 100 и округляет → "200 ₽"
    expect(screen.getByText(/Ловцу за поимку:/)).toBeInTheDocument();
    expect(screen.getByText("200 ₽")).toBeInTheDocument();
    // Warning НЕ показывается
    expect(screen.queryByText(/вся сумма штрафа уходит ловцу/)).toBeNull();
  });

  it(
    "показывает '⚠️ вся сумма штрафа уходит ловцу' при catcher >= penalty",
    () => {
      // penalty=300₽, catcher=400₽ → больше штрафа, warning
      const habit = makeHabit({
        penalty_amount: 30000,
        catcher_amount_kopecks: 40000,
      });
      renderCard(habit);

      expect(screen.getByText(/Ловцу за поимку:/)).toBeInTheDocument();
      expect(screen.getByText("400 ₽")).toBeInTheDocument();
      // Warning показывается
      expect(
        screen.getByText(/вся сумма штрафа уходит ловцу/),
      ).toBeInTheDocument();
      // Тултип — человеческий текст (без упоминаний apply_catch / функций)
      const warning = screen.getByTitle(
        "Если баланс нарушителя меньше штрафа, доли считаются от фактически списанной суммы",
      );
      expect(warning).toBeInTheDocument();
    },
  );

  it("edge case: catcher == penalty ровно → warning показывается", () => {
    // Граница: ровно == penalty → clamp → всё ловцу, warning
    const habit = makeHabit({
      penalty_amount: 30000,
      catcher_amount_kopecks: 30000,
    });
    renderCard(habit);

    expect(
      screen.getByText(/вся сумма штрафа уходит ловцу/),
    ).toBeInTheDocument();
  });
});
