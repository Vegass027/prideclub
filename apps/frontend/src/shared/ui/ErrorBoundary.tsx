import { Component, type ErrorInfo, type ReactNode } from "react";

import { ScreenLayout } from "@/shared/ui/ScreenLayout";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Без Sentry — локальный console: в проде подключится через pino/sentry-bridge
    // на стороне main.tsx (см. docs/09-prod-readiness.md, отложено).
    // PII не логируем: передаём только message и componentStack.
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary]", error.message, info.componentStack);
  }

  private handleReload = (): void => {
    window.location.reload();
  };

  render(): ReactNode {
    if (this.state.error === null) {
      return this.props.children;
    }
    return (
      <ScreenLayout>
        <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 text-center">
          <div className="text-2xl font-semibold text-text">
            Что-то пошло не так
          </div>
          <div className="max-w-md text-sm text-muted">
            Произошла непредвиденная ошибка. Попробуйте перезагрузить приложение.
            Если ошибка повторяется — напишите в поддержку.
          </div>
          <button
            type="button"
            onClick={this.handleReload}
            className="rounded-card bg-primary px-5 py-2.5 text-sm font-medium text-white transition active:scale-95"
          >
            Перезагрузить
          </button>
        </div>
      </ScreenLayout>
    );
  }
}
