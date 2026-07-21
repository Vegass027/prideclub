import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useMarketplace } from "@/shared/hooks";
import { BottomNav } from "@/shared/ui/BottomNav";
import { PageHeader } from "@/shared/ui/PageHeader";
import { Skeleton } from "@/shared/ui/Skeleton";
import { Button } from "@/shared/ui/Button";
import { EmptyState } from "@/shared/ui/EmptyState";

export function OnboardingPage() {
  const navigate = useNavigate();
  const { data, isLoading } = useMarketplace();

  useEffect(() => {
    if (data && data.items.length > 0) {
      navigate("/marketplace", { replace: true });
    }
  }, [data, navigate]);

  return (
    <main className="mx-auto max-w-md px-4 py-6">
      <PageHeader title="Добро пожаловать" />
      {isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : (
        <EmptyState
          icon="🎉"
          title="Это Habit Club"
          description="Закрытые клубы дисциплины: подтверждай привычку каждый день, не пропускай — деньги в призовом фонде клуба."
          action={
            <Button onClick={() => navigate("/marketplace")}>
              Выбрать клуб
            </Button>
          }
        />
      )}
      <BottomNav />
    </main>
  );
}
