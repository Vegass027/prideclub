import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useMyHabits } from "@/shared/hooks";
import { BottomNav } from "@/shared/ui/BottomNav";
import { ScreenLayout } from "@/shared/ui/ScreenLayout";
import { Skeleton } from "@/shared/ui/Skeleton";

export function OnboardingPage() {
  const navigate = useNavigate();
  const { data, isLoading } = useMyHabits();

  useEffect(() => {
    if (!data) return;
    if (data.items.length === 1) {
      navigate(`/habits/${data.items[0].id}/today`, { replace: true });
    } else if (data.items.length > 1) {
      navigate("/my-habits", { replace: true });
    }
  }, [data, navigate]);

  return (
    <ScreenLayout>
      {isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : (
        <div className="flex flex-col items-center justify-center pt-16 text-center">
          <span className="text-5xl" aria-hidden="true">🎉</span>
          <h1 className="mt-4 text-2xl font-bold">Добро пожаловать</h1>
          <p className="mt-2 max-w-xs text-sm text-muted">
            Закрытые клубы дисциплины: подтверждай привычку каждый день,
            не пропускай — деньги в призовом фонде клуба.
          </p>
          <button
            onClick={() => navigate("/marketplace")}
            className="mt-6 inline-flex items-center justify-center rounded-md bg-primary px-5 py-2.5 text-sm font-semibold text-canvas transition hover:bg-primary/90"
          >
            Выбрать клуб
          </button>
        </div>
      )}
      <BottomNav />
    </ScreenLayout>
  );
}
