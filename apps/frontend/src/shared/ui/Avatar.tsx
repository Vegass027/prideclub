interface AvatarProps {
  src?: string | null;
  fallback: string;
  size?: "sm" | "md" | "lg";
  className?: string;
  glow?: boolean;
  /**
   * Тонкая белая обводка для списков (Pravki §7.1 v3.1, юзер жаловался
   * что данные смешиваются). Применяется И к <img>, И к fallback-инициалам.
   * По умолчанию off — для одиночных аватаров (Today/Profile).
   */
  ring?: boolean;
  /**
   * eager: для списков лидерборда/участников (Pravki §7.1 v3.1).
   * Загружает аватар сразу, не откладывая до viewport — пользователь
   * уже видит список, lazy-load не нужен (только задержка).
   * Default lazy для остальных мест.
   */
  loading?: "lazy" | "eager";
}

const SIZES: Record<NonNullable<AvatarProps["size"]>, string> = {
  sm: "h-9 w-9 text-sm",
  md: "h-12 w-12 text-base",
  lg: "h-14 w-14 text-2xl",
};

// Тонкая белая обводка. ring-white/15 = 1px белый 15% alpha — видно на
// любом фоне, но не "кричит". ring-inset = внутри контейнера, не
// смещает layout.
const RING = "ring-1 ring-inset ring-white/15";

export function Avatar({
  src,
  fallback,
  size = "md",
  className = "",
  glow = false,
  ring = false,
  loading = "lazy",
}: AvatarProps) {
  const initials = fallback.charAt(0).toUpperCase() || "?";
  const glowRing = glow
    ? "ring-2 ring-primary/60 shadow-[0_0_18px_0_rgba(124,92,252,0.55)]"
    : "";
  const ringCls = ring ? RING : "";
  if (src) {
    return (
      <img
        src={src}
        alt={fallback}
        loading={loading}
        // decoding="async": не блокирует main thread при рендере множества
        // аватаров в лидерборде. На 160x160 после server-side resize эффект
        // минимален, но стандартная практика для списков.
        decoding="async"
        referrerPolicy="no-referrer"
        className={`${SIZES[size]} shrink-0 rounded-full object-cover ${ringCls} ${glowRing} ${className}`}
        onError={(e) => {
          const target = e.currentTarget;
          target.style.display = "none";
          const fallbackEl = target.nextElementSibling as HTMLElement | null;
          if (fallbackEl) fallbackEl.style.display = "flex";
        }}
      />
    );
  }
  return (
    <div
      className={`${SIZES[size]} flex shrink-0 items-center justify-center rounded-full bg-primary/20 font-bold text-primary ${ringCls} ${glowRing} ${className}`}
      aria-hidden="true"
    >
      {initials}
    </div>
  );
}
