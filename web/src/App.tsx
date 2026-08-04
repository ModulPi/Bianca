import { useEffect } from "react";
import { useSearchParams, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import CheckpointsPage from "./pages/CheckpointsPage";
import DashboardPage from "./pages/DashboardPage";
import SessionDetailPage from "./pages/SessionDetailPage";
import SessionsPage from "./pages/SessionsPage";
import TradesPage from "./pages/TradesPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="trades" element={<TradesPage />} />
        <Route path="sessions" element={<SessionsPage />} />
        <Route path="sessions/:id" element={<SessionDetailPage />} />
        <Route path="checkpoints" element={<CheckpointsPageWithQuery />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

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
