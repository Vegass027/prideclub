import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { EmptyState } from "@/shared/ui/EmptyState";
import { Skeleton } from "@/shared/ui/Skeleton";
import { ScreenLayout } from "@/shared/ui/ScreenLayout";
import { PageHeader } from "@/shared/ui/PageHeader";
import { AdminHabitCard } from "../components/AdminHabitCard";
import {
  useActivateHabit,
  useAdminHabits,
  useDeleteHabit,
  usePermanentDeleteHabit,
  useRestoreHabit,
} from "../hooks";
import type { AdminHabit } from "../api";

type Filter = "active" | "inactive" | "archived";

const FILTERS: { id: Filter; label: string; emoji: string }[] = [
  { id: "active", label: "Активные", emoji: "✅" },
  { id: "inactive", label: "Скрытые", emoji: "👁" },
  { id: "archived", label: "Архив", emoji: "🗂" },
];

const isFilter = (raw: string | null): raw is Filter =>
  raw === "active" || raw === "inactive" || raw === "archived";

function emptyTextFor(filter: Filter): {
  icon: string;
  title: string;
  description: string;
} {
  switch (filter) {
    case "archived":
      return {
        icon: "🗂",
        title: "Архив пуст",
        description: "Удалённые клубы появятся здесь.",
      };
    case "active":
      return {
        icon: "✅",
        title: "Нет активных клубов",
        description: "Создайте или активируйте клуб через переключатель.",
      };
    case "inactive":
      return {
        icon: "👁",
        title: "Нет скрытых клубов",
        description: "Скрытые клубы появятся здесь.",
      };
  }
}

const FormButtonSecondary = (
  <Link
    to="/habits/new"
    className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-card border border-white/10 bg-surface px-5 py-3 text-sm font-medium text-text transition hover:border-white/20"
  >
    <span aria-hidden="true">+</span>
    <span>Добавить клуб</span>
  </Link>
);

interface FilteredHabitsProps {
  data: AdminHabit[];
  filter: Filter;
  onToggle: (habitId: string, nextActive: boolean) => void;
  onDelete: (habitId: string) => void;
  onRestore: (habitId: string) => void;
  onPermanentDelete: (habitId: string) => void;
  busyHabitId: string;
  isBusy: boolean;
}

function FilteredHabits({
  data,
  filter,
  onToggle,
  onDelete,
  onRestore,
  onPermanentDelete,
  busyHabitId,
  isBusy,
}: FilteredHabitsProps) {
  const items = useMemo(() => {
    if (filter === "archived") return data.filter((h) => h.archived_at !== null);
    if (filter === "active") return data.filter((h) => h.archived_at === null && h.is_active);
    return data.filter((h) => h.archived_at === null && !h.is_active);
  }, [data, filter]);

  if (items.length === 0) {
    const emptyText = emptyTextFor(filter);
    return (
      <EmptyState
        icon={emptyText.icon}
        title={emptyText.title}
        description={emptyText.description}
      />
    );
  }

  return (
    <ul className="flex flex-col gap-3">
      {items.map((habit) => (
        <li key={habit.id}>
          <AdminHabitCard
            habit={habit}
            onToggle={(id, next) => onToggle(id, next)}
            onDelete={(id) => onDelete(id)}
            onRestore={(id) => onRestore(id)}
            onPermanentDelete={(id) => onPermanentDelete(id)}
            busy={busyHabitId === habit.id && isBusy}
          />
        </li>
      ))}
    </ul>
  );
}

export function HabitsListPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const rawFilter = searchParams.get("filter");
  const filter: Filter = isFilter(rawFilter) ? rawFilter : "active";

  const { data, isLoading, isError, error, refetch } = useAdminHabits();
  const activate = useActivateHabit();
  const delete_ = useDeleteHabit();
  const restore = useRestoreHabit();
  const permanentDelete = usePermanentDeleteHabit();

  const handleFilterChange = (next: Filter) => {
    const params = new URLSearchParams(searchParams);
    params.set("filter", next);
    setSearchParams(params, { replace: true });
  };

  const busyHabitId =
    (activate.variables?.habitId ??
      delete_.variables ??
      restore.variables ??
      permanentDelete.variables) ?? "";
  const isBusy =
    activate.isPending ||
    delete_.isPending ||
    restore.isPending ||
    permanentDelete.isPending;

  return (
    <ScreenLayout>
      <PageHeader title="Клубы" right={FormButtonSecondary} />

      <div className="mb-4 flex gap-1 rounded-card bg-surface/60 p-1" role="tablist">
        {FILTERS.map((f) => {
          const isActive = filter === f.id;
          return (
            <button
              key={f.id}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => handleFilterChange(f.id)}
              className={`flex-1 rounded-md px-3 py-2 text-sm font-medium transition ${
                isActive
                  ? "bg-primary text-white"
                  : "text-muted hover:bg-surface hover:text-text"
              }`}
            >
              <span aria-hidden="true">{f.emoji} </span>
              {f.label}
            </button>
          );
        })}
      </div>

      {isLoading && (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-36 w-full" />
          ))}
        </div>
      )}

      {isError && (
        <EmptyState
          icon="⚠️"
          title="Не удалось загрузить клубы"
          description={String(error)}
          action={
            <button
              type="button"
              onClick={() => refetch()}
              className="inline-flex min-h-[44px] items-center justify-center rounded-card bg-primary px-5 py-3 text-sm font-semibold text-white hover:opacity-90"
            >
              Повторить
            </button>
          }
        />
      )}

      {!isLoading && !isError && data && (
        <FilteredHabits
          data={data.items}
          filter={filter}
          onToggle={(id, next) => activate.mutate({ habitId: id, isActive: next })}
          onDelete={(id) => delete_.mutate(id)}
          onRestore={(id) => restore.mutate(id)}
          onPermanentDelete={(id) => permanentDelete.mutate(id)}
          busyHabitId={busyHabitId}
          isBusy={isBusy}
        />
      )}
    </ScreenLayout>
  );
}
