import type { HealthResponse } from "../types/api";

interface SystemHealthPanelProps {
  health: HealthResponse | null;
}

function badge(ok: boolean | undefined, label: string, detail?: string | null) {
  const tone = ok ? "text-emerald-400 ring-emerald-900" : "text-amber-400 ring-amber-900";
  return (
    <div className={`rounded-lg border border-zinc-800 bg-zinc-950/60 px-3 py-2 ring-1 ${tone}`}>
      <p className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</p>
      <p className="mono text-sm">{detail ?? "—"}</p>
    </div>
  );
}

export default function SystemHealthPanel({ health }: SystemHealthPanelProps) {
  if (!health) {
    return null;
  }

  const dbOk = health.database === "ok";
  const redisOk = health.redis === "ok" || health.redis === "memory";
  const cp = health.checkpointer_backend ?? "sqlite";
  const schema = health.schema_mode ?? "poc";

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 space-y-3">
      <p className="text-xs uppercase tracking-wide text-zinc-500">基础设施 · M7</p>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {badge(dbOk, "Database", health.database_backend ?? health.database)}
        {badge(schema === "mvp", "Schema", schema)}
        {badge(redisOk, "Redis", health.redis ?? "—")}
        {badge(true, "Checkpointer", cp)}
      </div>
    </div>
  );
}
