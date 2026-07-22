import { Navigate, Route, Routes } from "react-router-dom";
import { HabitsListPage } from "./pages/HabitsListPage";
import { HabitCreatePage } from "./pages/HabitCreatePage";
import { HabitEditPage } from "./pages/HabitEditPage";

export function AdminAppRouter() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/habits" replace />} />
      <Route path="/habits" element={<HabitsListPage />} />
      <Route path="/habits/new" element={<HabitCreatePage />} />
      <Route path="/habits/:habitId/edit" element={<HabitEditPage />} />
      <Route path="*" element={<Navigate to="/habits" replace />} />
    </Routes>
  );
}
