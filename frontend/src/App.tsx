import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import DashboardPage from "./pages/DashboardPage";
import DecisionsPage from "./pages/DecisionsPage";
import DialoguePage from "./pages/DialoguePage";
import GoalDetailPage from "./pages/GoalDetailPage";
import GoalsPage from "./pages/GoalsPage";
import MarketPage from "./pages/MarketPage";
import PositionsPage from "./pages/PositionsPage";
import RiskPage from "./pages/RiskPage";
import TradesPage from "./pages/TradesPage";
import UsagePage from "./pages/UsagePage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="dialogue" element={<DialoguePage />} />
        <Route path="goals" element={<GoalsPage />} />
        <Route path="goals/:id" element={<GoalDetailPage />} />
        <Route path="trades" element={<TradesPage />} />
        <Route path="positions" element={<PositionsPage />} />
        <Route path="decisions" element={<DecisionsPage />} />
        <Route path="risk" element={<RiskPage />} />
        <Route path="market" element={<MarketPage />} />
        <Route path="usage" element={<UsagePage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
