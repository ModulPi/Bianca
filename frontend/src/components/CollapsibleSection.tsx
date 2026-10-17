import { useState, type ReactNode } from "react";

interface CollapsibleSectionProps {
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
  badge?: string;
}

export default function CollapsibleSection({
  title,
  children,
  defaultOpen = true,
  badge,
}: CollapsibleSectionProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-900/30">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-zinc-900/50"
      >
        <span className="flex items-center gap-2 text-sm font-medium text-zinc-300">
          {title}
          {badge ? (
            <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] font-normal text-zinc-500">
              {badge}
            </span>
          ) : null}
        </span>
        <span className="text-xs text-zinc-500">{open ? "收起" : "展开"}</span>
      </button>
      {open ? <div className="border-t border-zinc-800 p-4 pt-3">{children}</div> : null}
    </section>
  );
}
