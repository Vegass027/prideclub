// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { SubscriptionBadge } from "@/shared/ui/SubscriptionBadge";

describe("SubscriptionBadge (Pravki-subscription-2026-08-17 §Frontend)", () => {
  it("ok → ничего не рендерит (UI показывает 'Членство до {date}' отдельно)", () => {
    const { container } = render(<SubscriptionBadge state={{ kind: "ok" }} />);
    expect(container.firstChild).toBeNull();
  });

  it("soon с daysLeft=2 → warning-бейдж 'через 2 дня'", () => {
    render(<SubscriptionBadge state={{ kind: "soon", daysLeft: 2 }} />);
    const badge = screen.getByRole("status", {
      name: "Подписка закончится через 2 дня",
    });
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent("через 2 дня");
    // warning-стили (жёлтый).
    expect(badge.className).toContain("bg-warning");
    expect(badge.className).toContain("text-warning");
  });

  it("soon с daysLeft=1 → warning-бейдж 'через 1 день'", () => {
    render(<SubscriptionBadge state={{ kind: "soon", daysLeft: 1 }} />);
    const badge = screen.getByRole("status", {
      name: "Подписка закончится через 1 день",
    });
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent("через 1 день");
  });

  it("expired → error-бейдж 'Подписка окончена'", () => {
    render(<SubscriptionBadge state={{ kind: "expired" }} />);
    const badge = screen.getByRole("status", { name: "Подписка окончена" });
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent("🚫");
    // error-стили (красный).
    expect(badge.className).toContain("bg-danger");
    expect(badge.className).toContain("text-danger");
  });

  it("size='md' использует rounded-card стили (для баннера на TodayPage)", () => {
    render(
      <SubscriptionBadge state={{ kind: "expired" }} size="md" />,
    );
    const badge = screen.getByRole("status", { name: "Подписка окончена" });
    // rounded-card — для крупного баннера, не для inline-бейджа.
    expect(badge.className).toContain("rounded-card");
  });

  it("size='sm' использует rounded-full стили (для inline-бейджа в ProfilePage)", () => {
    render(
      <SubscriptionBadge state={{ kind: "expired" }} size="sm" />,
    );
    const badge = screen.getByRole("status", { name: "Подписка окончена" });
    expect(badge.className).toContain("rounded-full");
  });
});