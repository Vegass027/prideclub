import { Navigate, Route, Routes } from "react-router-dom";
import { MarketplacePage } from "@/pages/Marketplace/MarketplacePage";
import { TodayPage } from "@/pages/Today/TodayPage";
import { MembersPage } from "@/pages/Members/MembersPage";
import { LeaderboardPage } from "@/pages/Leaderboard/LeaderboardPage";
import { GlobalLeaderboardPage } from "@/pages/GlobalLeaderboard/GlobalLeaderboardPage";
import { ProfilePage } from "@/pages/Profile/ProfilePage";
import { OnboardingPage } from "@/pages/Onboarding/OnboardingPage";
import { CharacterPage } from "@/pages/Character";

export function AppRouter() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/marketplace" replace />} />
      <Route path="/onboarding" element={<OnboardingPage />} />
      <Route path="/marketplace" element={<MarketplacePage />} />
      <Route path="/my-habits" element={<Navigate to="/profile" replace />} />
      <Route path="/habits/:habitId/today" element={<TodayPage />} />
      <Route path="/habits/:habitId/members" element={<MembersPage />} />
      <Route path="/habits/:habitId/leaderboard" element={<LeaderboardPage />} />
      <Route path="/leaderboards" element={<GlobalLeaderboardPage />} />
      <Route path="/profile" element={<ProfilePage />} />
      <Route path="/character" element={<CharacterPage />} />
      <Route path="*" element={<Navigate to="/marketplace" replace />} />
    </Routes>
  );
}
