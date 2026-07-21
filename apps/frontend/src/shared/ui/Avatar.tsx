interface AvatarProps {
  src?: string | null;
  fallback: string;
  size?: "sm" | "md" | "lg";
  className?: string;
}

const SIZES: Record<NonNullable<AvatarProps["size"]>, string> = {
  sm: "h-9 w-9 text-sm",
  md: "h-12 w-12 text-base",
  lg: "h-14 w-14 text-2xl",
};

export function Avatar({ src, fallback, size = "md", className = "" }: AvatarProps) {
  const initials = fallback.charAt(0).toUpperCase() || "?";
  if (src) {
    return (
      <img
        src={src}
        alt={fallback}
        loading="lazy"
        referrerPolicy="no-referrer"
        className={`${SIZES[size]} shrink-0 rounded-full object-cover ${className}`}
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
      className={`${SIZES[size]} flex shrink-0 items-center justify-center rounded-full bg-primary/20 font-bold text-primary ${className}`}
      aria-hidden="true"
    >
      {initials}
    </div>
  );
}
