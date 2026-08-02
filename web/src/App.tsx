import { useEffect } from "react";
import { useSearchParams, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import CheckpointsPage from "./pages/CheckpointsPage";
import DashboardPage from "./pages/DashboardPage";
import DecisionsPage from "./pages/DecisionsPage";
import RiskPage from "./pages/RiskPage";
import SessionDetailPage from "./pages/SessionDetailPage";
import SessionsPage from "./pages/SessionsPage";
import StrategiesPage from "./pages/StrategiesPage";
import TradesPage from "./pages/TradesPage";
import UsagePage from "./pages/UsagePage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="trades" element={<TradesPage />} />
        <Route path="sessions" element={<SessionsPage />} />
        <Route path="sessions/:id" element={<SessionDetailPage />} />
        <Route path="strategies" element={<StrategiesPage />} />
        <Route path="usage" element={<UsagePage />} />
        <Route path="decisions" element={<DecisionsPage />} />
        <Route path="risk" element={<RiskPage />} />
        <Route path="checkpoints" element={<CheckpointsPageWithQuery />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

/** 支持 /checkpoints?thread=xxx 预选线程 */
function CheckpointsPageWithQuery() {
  const [params] = useSearchParams();
  const thread = params.get("thread");
  useEffect(() => {
    if (thread) {
      sessionStorage.setItem("bianca.checkpoint.thread", thread);
    }
  }, [thread]);
  return <CheckpointsPage initialThread={thread} />;
}
