// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusBadge } from "@/shared/ui/StatusBadge";

describe("StatusBadge (Pravki-bug-fixes §Z-21 — caught badge)", () => {
  it.each([
    { status: "done" as const, label: "Выполнено", emoji: "✅" },
    { status: "missed" as const, label: "Просрочено", emoji: "❌" },
    { status: "pending" as const, label: "Ожидает выполнения", emoji: "⏳" },
    { status: "not_started" as const, label: "Не начато", emoji: "💤" },
    { status: "joined_late" as const, label: "Присоединился поздно", emoji: "🌙" },
    // Z-21: новый статус для пойманных. Лейбл «Пойман» с 🎯, danger-классы.
    { status: "caught" as const, label: "Пойман", emoji: "🎯" },
  ])("рендерит $status → $label $emoji", ({ status, label, emoji }) => {
    render(<StatusBadge status={status} />);
    const badge = screen.getByRole("status", { name: label });
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent(emoji);
  });

  it("caught использует danger-классы (как missed)", () => {
    render(<StatusBadge status="caught" />);
    const badge = screen.getByRole("status", { name: "Пойман" });
    // danger стилизация визуально как missed, но emoji/label различают сценарии
    expect(badge.className).toContain("bg-danger");
    expect(badge.className).toContain("text-danger");
  });

  it("fallback для неизвестного статуса (расширение backend вперёд frontend)", () => {
    // Защита от рассинхрона: если backend добавит новый статус раньше frontend,
    // badge рендерится с fallback ('—') без crash.
    render(<StatusBadge status={"unknown_future_status" as never} />);
    const badge = screen.getByRole("status", { name: "—" });
    expect(badge).toBeInTheDocument();
  });
});