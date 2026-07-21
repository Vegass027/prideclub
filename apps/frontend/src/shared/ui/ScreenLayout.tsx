import type { ReactNode } from "react";

interface ScreenLayoutProps {
  children: ReactNode;
}

export function ScreenLayout({ children }: ScreenLayoutProps) {
  return (
    <main className="mx-auto max-w-md px-4 pb-24 pt-4">
      {children}
    </main>
  );
}
