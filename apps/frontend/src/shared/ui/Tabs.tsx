interface TabsProps<T extends string> {
  tabs: { id: T; label: string; emoji?: string }[];
  active: T;
  onChange: (id: T) => void;
}

export function Tabs<T extends string>({ tabs, active, onChange }: TabsProps<T>) {
  return (
    <div className="mb-3 flex gap-1 rounded-card bg-surface/60 p-1" role="tablist">
      {tabs.map((tab) => {
        const isActive = tab.id === active;
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => onChange(tab.id)}
            className={`flex-1 rounded-md px-3 py-2 text-sm font-medium transition ${
              isActive
                ? "bg-primary text-white"
                : "text-muted hover:bg-surface hover:text-text"
            }`}
          >
            {tab.emoji && <span aria-hidden="true">{tab.emoji} </span>}
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
