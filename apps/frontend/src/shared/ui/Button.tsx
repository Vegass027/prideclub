import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "danger" | "ghost";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  loading?: boolean;
  children: ReactNode;
}

const variants: Record<Variant, string> = {
  primary: "bg-primary text-white hover:opacity-90",
  secondary: "bg-surface text-text border border-white/10 hover:border-white/20",
  danger: "bg-danger text-white hover:opacity-90",
  ghost: "text-muted hover:text-text",
};

export function Button({
  variant = "primary",
  loading = false,
  disabled,
  className = "",
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      aria-busy={loading}
      className={`inline-flex min-h-[44px] items-center justify-center rounded-card px-5 py-3 text-sm font-semibold transition active:scale-[0.98] disabled:opacity-50 ${variants[variant]} ${className}`}
    >
      {loading ? <span className="opacity-70">...</span> : children}
    </button>
  );
}