interface StatCardProps {
  label: string;
  value: string;
  hint?: string;
  tone?: "default" | "positive" | "negative" | "warn";
}

const toneClass = {
  default: "text-zinc-100",
  positive: "text-emerald-400",
  negative: "text-rose-400",
  warn: "text-amber-400",
};

export default function StatCard({ label, value, hint, tone = "default" }: StatCardProps) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
      <p className="text-xs uppercase tracking-wide text-zinc-500">{label}</p>
      <p className={`mono mt-1 text-2xl font-semibold ${toneClass[tone]}`}>{value}</p>
      {hint ? <p className="mt-1 text-xs text-zinc-500">{hint}</p> : null}
    </div>
  );
}
