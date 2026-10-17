import { NavLink, Outlet } from "react-router-dom";

const GROUPS = [
  {
    title: "总览",
    links: [{ to: "/", label: "看板", end: true }],
  },
  {
    title: "对话",
    links: [
      { to: "/dialogue", label: "当前目标", end: false },
      { to: "/goals", label: "目标历史", end: false },
    ],
  },
  {
    title: "账本",
    links: [
      { to: "/trades", label: "交易明细", end: false },
      { to: "/positions", label: "持仓 · 权益", end: false },
      { to: "/decisions", label: "决策回放", end: false },
    ],
  },
  {
    title: "风控",
    links: [{ to: "/risk", label: "风控事件", end: false }],
  },
  {
    title: "数据",
    links: [
      { to: "/market", label: "行情", end: false },
      { to: "/usage", label: "Token 用量", end: false },
    ],
  },
] as const;

export default function Layout() {
  return (
    <div className="flex min-h-screen">
      <aside className="flex w-56 shrink-0 flex-col gap-6 border-r border-zinc-800 bg-zinc-900/50 p-4">
        <div>
          <p className="text-xs uppercase tracking-widest text-zinc-500">Bianca</p>
          <h1 className="text-lg font-semibold text-amber-400">Console</h1>
        </div>
        <nav className="flex flex-col gap-4">
          {GROUPS.map((group) => (
            <div key={group.title}>
              <p className="mb-1 px-3 text-[10px] uppercase tracking-widest text-zinc-600">
                {group.title}
              </p>
              <div className="flex flex-col gap-0.5">
                {group.links.map((link) => (
                  <NavLink
                    key={link.to}
                    to={link.to}
                    end={link.end}
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
              </div>
            </div>
          ))}
        </nav>
        <p className="mt-auto text-[10px] leading-relaxed text-zinc-600">
          只绑 127.0.0.1 · 无鉴权
          <br />
          界面只读，唯一写入口是对话
        </p>
      </aside>
      <main className="flex-1 overflow-auto p-6">
        <Outlet />
      </main>
    </div>
  );
}
