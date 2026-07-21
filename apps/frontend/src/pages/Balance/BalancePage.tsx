import { useBalance } from "@/shared/hooks";
import { Button } from "@/shared/ui/Button";

const fmt = (k: number) =>
  new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 0 }).format(k / 100);

export function BalancePage() {
  const { data, isLoading } = useBalance();
  if (isLoading || !data) return <div className="mx-auto max-w-md px-4 py-6 text-sm text-muted">Загрузка...</div>;

  const lowBalance = data.deposit_balance < 500_00;
  return (
    <main className="mx-auto max-w-md px-4 py-6">
      <h1 className="mb-4 text-2xl font-bold">Баланс</h1>
      <section className="mb-6 rounded-card bg-surface p-6">
        <div className={`text-5xl font-bold ${lowBalance ? "text-danger" : "text-success"}`}>{fmt(data.deposit_balance)}</div>
        <div className="mt-1 text-sm text-muted">текущий депозит</div>
        <div className="mt-4 flex gap-2">
          <Button variant="primary">Пополнить</Button>
          <Button variant="secondary">Вывести</Button>
        </div>
      </section>
      <h2 className="mb-2 text-base font-semibold">История операций</h2>
      <ul className="flex flex-col gap-1">
        {data.history.map((t) => (
          <li key={t.id} className="flex items-center justify-between rounded-card bg-surface p-3">
            <div className="text-sm">{t.type}</div>
            <div className={`text-sm font-semibold ${t.amount < 0 ? "text-danger" : "text-success"}`}>
              {t.amount < 0 ? "−" : "+"}
              {fmt(Math.abs(t.amount))}
            </div>
          </li>
        ))}
        {data.history.length === 0 && (
          <li className="rounded-card bg-surface p-4 text-center text-sm text-muted">операций пока нет</li>
        )}
      </ul>
    </main>
  );
}