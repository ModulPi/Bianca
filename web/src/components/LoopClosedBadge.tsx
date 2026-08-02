export default function LoopClosedBadge({ closed }: { closed: boolean }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
        closed
          ? "bg-emerald-950 text-emerald-400 ring-1 ring-emerald-800"
          : "bg-zinc-800 text-zinc-400 ring-1 ring-zinc-700"
      }`}
    >
      {closed ? "闭环完成" : "闭环未完成"}
    </span>
  );
}
