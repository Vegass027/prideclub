import { useNavigate } from "react-router-dom";

export function OnboardingPage() {
  const navigate = useNavigate();
  return (
    <main className="mx-auto max-w-md px-4 py-6">
      <h1 className="mb-2 text-2xl font-bold">Добро пожаловать</h1>
      <p className="mb-6 text-sm text-muted">
        Обещай — выполняй — зарабатывай. Закрытые клубы привычек с денежными штрафами.
      </p>
      <button onClick={() => navigate("/marketplace")} className="w-full rounded-card bg-primary p-4 text-base font-semibold text-white">
        Выбрать привычку
      </button>
    </main>
  );
}