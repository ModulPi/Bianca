import { useEffect } from "react";
import { useSearchParams, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import RequireAuth from "./components/RequireAuth";
import ChatPage from "./pages/ChatPage";
import CheckpointsPage from "./pages/CheckpointsPage";
import DashboardPage from "./pages/DashboardPage";
import DecisionsPage from "./pages/DecisionsPage";
import LoginPage from "./pages/LoginPage";
import PositionsPage from "./pages/PositionsPage";
import RiskPage from "./pages/RiskPage";
import SessionDetailPage from "./pages/SessionDetailPage";
import SessionsPage from "./pages/SessionsPage";
import StrategiesPage from "./pages/StrategiesPage";
import TradesPage from "./pages/TradesPage";
import UsagePage from "./pages/UsagePage";
import ValidationPage from "./pages/ValidationPage";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route element={<Layout />}>
          <Route index element={<DashboardPage />} />
          <Route path="chat" element={<ChatPage />} />
          <Route path="trades" element={<TradesPage />} />
          <Route path="positions" element={<PositionsPage />} />
          <Route path="sessions" element={<SessionsPage />} />
          <Route path="sessions/:id" element={<SessionDetailPage />} />
          <Route path="strategies" element={<StrategiesPage />} />
          <Route path="usage" element={<UsagePage />} />
          <Route path="decisions" element={<DecisionsPage />} />
          <Route path="risk" element={<RiskPage />} />
          <Route path="checkpoints" element={<CheckpointsPageWithQuery />} />
          <Route path="validation" element={<ValidationPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
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
