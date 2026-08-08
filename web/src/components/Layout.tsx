import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { getApiToken, setApiToken } from "../api/token";

const links = [
  { to: "/", label: "运维看板" },
  { to: "/chat", label: "聊天指挥" },
  { to: "/positions", label: "持仓" },
  { to: "/trades", label: "交易" },
  { to: "/sessions", label: "会话" },
  { to: "/strategies", label: "策略" },
  { to: "/checkpoints", label: "回放" },
  { to: "/decisions", label: "决策" },
  { to: "/risk", label: "风控" },
  { to: "/validation", label: "门禁" },
  { to: "/usage", label: "Token" },
];

export default function Layout() {
  const navigate = useNavigate();
  const hasToken = Boolean(getApiToken());

  function logout() {
    setApiToken("");
    navigate("/login", { replace: true });
  }

  return (
    <div className="min-h-screen flex">
      <aside className="w-56 shrink-0 border-r border-zinc-800 bg-zinc-900/50 p-4 flex flex-col gap-6">
        <div>
          <p className="text-xs uppercase tracking-widest text-zinc-500">Bianca</p>
          <h1 className="text-lg font-semibold text-amber-400">Console</h1>
        </div>
        <nav className="flex flex-col gap-1">
          {links.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.to === "/"}
              className={({ isActive }) =>
                `rounded-lg px-3 py-2 text-sm transition-colors ${
                  isActive
                    ? "bg-zinc-800 text-amber-300"
                    : "text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-200"
                }`
              }
            >
              {link.label}
            </NavLink>
          ))}
        </nav>
        {hasToken ? (
          <button
            type="button"
            onClick={logout}
            className="text-xs text-zinc-500 hover:text-zinc-300 text-left"
          >
            退出登录
          </button>
        ) : null}
        <p className="mt-auto text-xs text-zinc-600">Agent Console · M9</p>
      </aside>
      <main className="flex-1 p-6 overflow-auto">
        <Outlet />
      </main>
    </div>
  );
}
