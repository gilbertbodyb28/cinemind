import { Film } from "lucide-react";

export default function EmptyState({
  title,
  description,
  action,
  icon: Icon = Film,
  testId,
}) {
  return (
    <section
      className="glass rounded-2xl flex min-h-64 flex-col items-center justify-center px-6 py-12 text-center"
      data-testid={testId}
    >
      <span className="flex h-14 w-14 items-center justify-center rounded-2xl border border-white/10 bg-white/[0.04] text-[#8C7F6D]">
        <Icon size={24} />
      </span>
      <h2 className="mt-5 font-display text-xl font-bold">{title}</h2>
      <p className="mt-2 max-w-md text-sm leading-6 text-slate-500">{description}</p>
      {action ? <div className="mt-6">{action}</div> : null}
    </section>
  );
}
