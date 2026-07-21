import { NavLink as RouterLink } from "react-router-dom";

const ITEMS: { to: string; emoji: string; label: string }[] = [
  { to: "/marketplace", emoji: "🏪", label: "Клубы" },
  { to: "/leaderboards", emoji: "🏆", label: "Лидеры" },
  { to: "/profile", emoji: "👤", label: "Профиль" },
];

export function BottomNav() {
  return (
    <nav
      className="fixed inset-x-0 bottom-0 z-40 border-t border-white/5 bg-canvas/95 px-2 pb-[env(safe-area-inset-bottom)] pt-2 backdrop-blur"
      role="navigation"
      aria-label="Главная навигация"
    >
      <ul className="mx-auto grid max-w-md grid-cols-3 gap-1">
        {ITEMS.map((item) => (
          <li key={item.to}>
            <RouterLink
              to={item.to}
              end
              className={({ isActive }) =>
                `flex flex-col items-center justify-center gap-0.5 rounded-md py-2 text-xs font-medium transition ${
                  isActive
                    ? "bg-primary/15 text-primary"
                    : "text-muted hover:bg-surface hover:text-text"
                }`
              }
            >
              <span className="text-lg" aria-hidden="true">
                {item.emoji}
              </span>
              <span>{item.label}</span>
            </RouterLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
