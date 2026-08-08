import { useEffect, useState } from "react";
import { Navigate, Outlet } from "react-router-dom";
import { api } from "../api/client";
import { getApiToken } from "../api/token";

export default function RequireAuth() {
  const [authRequired, setAuthRequired] = useState<boolean | null>(null);

  useEffect(() => {
    void api
      .health()
      .then((h) => setAuthRequired(h.api_auth_enabled))
      .catch(() => setAuthRequired(false));
  }, []);

  if (authRequired === null) {
    return <p className="p-6 text-sm text-zinc-500">加载中…</p>;
  }

  if (authRequired && !getApiToken()) {
    return <Navigate to="/login" replace />;
  }

  return <Outlet />;
}
