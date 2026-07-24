interface AvatarProps {
  src?: string | null;
  fallback: string;
  size?: "sm" | "md" | "lg";
  className?: string;
  glow?: boolean;
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

export function Avatar({
  src,
  fallback,
  size = "md",
  className = "",
  glow = false,
  loading = "lazy",
}: AvatarProps) {
  const initials = fallback.charAt(0).toUpperCase() || "?";
  const ring = glow ? "ring-2 ring-primary/60 shadow-[0_0_18px_0_rgba(124,92,252,0.55)]" : "";
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
        className={`${SIZES[size]} shrink-0 rounded-full object-cover ${ring} ${className}`}
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
      className={`${SIZES[size]} flex shrink-0 items-center justify-center rounded-full bg-primary/20 font-bold text-primary ${ring} ${className}`}
      aria-hidden="true"
    >
      {initials}
    </div>
  );
}
