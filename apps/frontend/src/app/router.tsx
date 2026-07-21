import { Navigate, Route, Routes } from "react-router-dom";
import { MarketplacePage } from "@/pages/Marketplace/MarketplacePage";
import { TodayPage } from "@/pages/Today/TodayPage";
import { MembersPage } from "@/pages/Members/MembersPage";
import { LeaderboardPage } from "@/pages/Leaderboard/LeaderboardPage";
import { BalancePage } from "@/pages/Balance/BalancePage";
import { ProfilePage } from "@/pages/Profile/ProfilePage";
import { OnboardingPage } from "@/pages/Onboarding/OnboardingPage";

export function AppRouter() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/marketplace" replace />} />
      <Route path="/onboarding" element={<OnboardingPage />} />
      <Route path="/marketplace" element={<MarketplacePage />} />
      <Route path="/today/:habitId" element={<TodayPage />} />
      <Route path="/members/:habitId" element={<MembersPage />} />
      <Route path="/leaderboard/:habitId" element={<LeaderboardPage />} />
      <Route path="/balance" element={<BalancePage />} />
      <Route path="/profile" element={<ProfilePage />} />
      <Route path="*" element={<Navigate to="/marketplace" replace />} />
    </Routes>
  );
}