import { useNavigate } from "react-router-dom";

export function ProfilePage() {
  const navigate = useNavigate();
  return (
    <main className="mx-auto max-w-md px-4 py-6">
      <h1 className="mb-4 text-2xl font-bold">Профиль</h1>
      <button
        onClick={() => navigate("/balance")}
        className="mb-2 block w-full rounded-card bg-surface p-4 text-left text-sm"
      >
        💰 Баланс и депозит
      </button>
      <button
        onClick={() => alert("Команда /delete_my_data будет отправлена боту")}
        className="block w-full rounded-card bg-surface p-4 text-left text-sm text-danger"
      >
        Удалить мои данные
      </button>
    </main>
  );
}