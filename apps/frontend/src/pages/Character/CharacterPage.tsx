import { EmptyState } from "@/shared/ui/EmptyState";
import { PageHeader } from "@/shared/ui/PageHeader";
import { ScreenLayout } from "@/shared/ui/ScreenLayout";
import { Skeleton } from "@/shared/ui/Skeleton";
import { useCharacter, useLevelUpStatus } from "@/shared/hooks";
import { FrozenStatBanner } from "./FrozenStatBanner";
import { LevelUpToast } from "./LevelUpToast";
import { StatCard } from "./StatCard";
import { StatusBadge } from "./StatusBadge";

/**
 * Phase 3 v2 Task 3.9: главная страница персонажа `/character`.
 *
 * Композиция:
 * 1. <StatusBadge> — top-card: icon + name + progress bar.
 * 2. <LevelUpToast> — overlay (visible=true при level-up).
 * 3. <FrozenStatBanner> — если есть frozen stats.
 * 4. <StatCard> × N — список характеристик
 *    (frozen внизу, иначе по value DESC).
 * 5. EmptyState если stats пустой.
 *
 * ⚠️ CRITICAL (Task 3.9 фокус Dmitry):
 * - useLevelUpStatus получает `character?.status.name ?? ""` и
 *   `character?.total_value ?? 0`. На loading/error первый рендер
 *   передаёт "" / 0 → calibration hook'а НЕ срабатывает
 *   (currentName === "" — guard внутри hook'а). На первом успешном
 *   fetch hook получит реальные значения и calibrate'нет.
 * - Если бы CharacterPage передавала derived (например, total || 0
 *   принудительно), то при ошибке после первого успеха рассинхронизация
 *   была бы гарантирована.
 * - Поэтому здесь — straight `?? 0`, не derived safety.
 */
export function CharacterPage() {
  const { data: character, isLoading, isError, error } = useCharacter();

  // ⚠️ Calibration-friendly: на loading оба — sentinel values ("" / 0),
  // hook их игнорирует (см. calibration guard).
  const { justLeveledUp, previousName, acknowledgeLevelUp } = useLevelUpStatus(
    character?.status.name ?? "",
    character?.total_value ?? 0,
  );

  // Сортировка: frozen внизу, активные по value DESC.
  const sortedStats = character
    ? [...character.stats].sort((a, b) => {
        if (a.is_frozen !== b.is_frozen) return a.is_frozen ? 1 : -1;
        return b.value - a.value;
      })
    : [];

  return (
    <ScreenLayout>
      <PageHeader title="Мой персонаж" back backTo="/profile" />

      <LevelUpToast
        visible={justLeveledUp}
        previousName={previousName}
        newName={character?.status.name ?? ""}
        onDone={acknowledgeLevelUp}
      />

      {isLoading && <Skeleton className="h-32 w-full" rows={2} />}

      {isError && (
        <EmptyState
          icon="⚠️"
          title="Не удалось загрузить персонажа"
          description={String(error)}
        />
      )}

      {!isLoading && !isError && character && (
        <>
          <StatusBadge
            status={character.status}
            total={character.total_value}
          />

          <FrozenStatBanner stats={character.stats} />

          <section className="mt-4">
            <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
              Характеристики
            </h2>
            {sortedStats.length === 0 ? (
              <EmptyState
                icon="🌱"
                title="Пока пусто"
                description="Сделай первый чек-ин, чтобы открыть характеристику"
              />
            ) : (
              <ul className="space-y-2">
                {sortedStats.map((stat) => (
                  <li key={stat.stat_definition_id}>
                    <StatCard stat={stat} />
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </ScreenLayout>
  );
}