import { NavLink as RouterLink, useParams } from "react-router-dom";

interface BottomNavProps {
  habitId: string;
}

export function BottomNav({ habitId }: BottomNavProps) {
  const items: { to: string; emoji: string; label: string }[] = [
    { to: `/today/${habitId}`, emoji: "📅", label: "Сегодня" },
    { to: `/members/${habitId}`, emoji: "👥", label: "Участники" },
    { to: `/leaderboard/${habitId}`, emoji: "🏆", label: "Лидеры" },
    { to: `/profile`, emoji: "👤", label: "Профиль" },
  ];

  return (
    <nav
      className="sticky bottom-0 left-0 right-0 -mx-4 mt-4 border-t border-white/5 bg-canvas/95 px-2 py-2 backdrop-blur"
      role="navigation"
      aria-label="Главная навигация"
    >
      <ul className="grid grid-cols-4 gap-1">
        {items.map((item) => (
          <li key={item.to}>
            <RouterLink
              to={item.to}
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

interface NavOutletProps {
  children: React.ReactNode;
}

export function NavOutlet({ children }: NavOutletProps) {
  const { habitId } = useParams<{ habitId: string }>();
  return (
    <>
      {children}
      {habitId && <BottomNav habitId={habitId} />}
    </>
  );
}
