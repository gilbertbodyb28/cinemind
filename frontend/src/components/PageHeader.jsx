export default function PageHeader({ eyebrow, title, description, actions, children }) {
  return (
    <header className="mb-8 flex flex-col gap-5 sm:mb-10 sm:flex-row sm:items-end sm:justify-between">
      <div className="max-w-3xl">
        {eyebrow ? <span className="chip chip-rose mb-3">{eyebrow}</span> : null}
        <h1 className="font-display text-4xl font-extrabold tracking-tight sm:text-5xl">
          {title}
        </h1>
        {description ? (
          <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-400 sm:text-base">
            {description}
          </p>
        ) : null}
        {children}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-3">{actions}</div> : null}
    </header>
  );
}
