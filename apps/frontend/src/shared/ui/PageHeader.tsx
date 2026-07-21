import type { ReactNode } from "react";

interface PageHeaderProps {
  title: string;
  subtitle?: string;
  back?: boolean;
  right?: ReactNode;
}

export function PageHeader({ title, subtitle, back, right }: PageHeaderProps) {
  const handleBack = () => {
    if (window.history.length > 1) window.history.back();
    else window.location.href = "/marketplace";
  };

  return (
    <header className="sticky top-0 z-10 -mx-4 mb-4 flex items-center gap-3 border-b border-white/5 bg-canvas/80 px-4 py-3 backdrop-blur">
      {back && (
        <button
          type="button"
          onClick={handleBack}
          className="flex h-9 w-9 items-center justify-center rounded-full text-text transition hover:bg-surface"
          aria-label="Назад"
        >
          <span aria-hidden="true">←</span>
        </button>
      )}
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-lg font-bold text-text">{title}</h1>
        {subtitle && <p className="truncate text-xs text-muted">{subtitle}</p>}
      </div>
      {right && <div className="flex items-center gap-2">{right}</div>}
    </header>
  );
}
