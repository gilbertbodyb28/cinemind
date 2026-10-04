import { Check } from "lucide-react";
import { RELEASE_CHECKS, TYPE_CHECKS } from "@/lib/mediaFilters";

/** The filter chips, larger (Gilbert, 2026-09-29: "förstoras en hel del så man ser det
 *  tydligt", then "gör det till 20px"). Same .chip look, only the size: .chip comes
 *  after the utilities in index.css, so the sizes need Tailwind's important modifier. */
export const BIG_CHIP = "!text-xl !px-4 !py-2.5 !gap-2.5";

/** The Logs filter chip with a tick box in front of the label. */
function CheckChip({ on, label, count, onToggle, testid, title }) {
  return (
    <button
      type="button"
      data-testid={testid}
      aria-pressed={on}
      title={title}
      onClick={onToggle}
      // text-left: at 20 px "Upcoming titles" wraps on a phone, and reads better from the left.
      className={`chip ${BIG_CHIP} text-left transition-colors flex items-center ${on ? "chip-rose" : "hover:chip-rose"}`}
    >
      {on ? (
        <Check className="w-6 h-6 text-[#D8B26A]" />
      ) : (
        <span className="w-6 h-6 rounded-[4px] border border-[rgba(255,240,220,0.35)]" />
      )}
      {label}
      {count != null && <span className="font-mono opacity-70">{count}</span>}
    </button>
  );
}

/**
 * Two rows of tick boxes: what is out and what is still coming, and which kinds
 * of media to keep. Ticking nothing in a row means that row narrows nothing, so
 * "Upcoming" on its own shows every upcoming title whatever the media type.
 */
export default function ReleaseTypeFilters({
  prefix,
  release,
  onToggleRelease,
  types,
  onToggleType,
  releaseCounts = {},
  typeCounts = {},
  className = "",
}) {
  return (
    <div data-testid={`${prefix}-release-type-filters`} className={`flex flex-col gap-3 ${className}`}>
      <div>
        <p className="font-mono text-sm uppercase tracking-wider text-[#8C7F6D] mb-2.5">Release</p>
        <div className="flex flex-wrap gap-2">
          {RELEASE_CHECKS.map(({ key, label }) => (
            <CheckChip
              key={key}
              testid={`${prefix}-release-${key}`}
              on={release.has(key)}
              label={label}
              count={releaseCounts[key] ?? 0}
              onToggle={() => onToggleRelease(key)}
              title={
                key === "upcoming"
                  ? "Titles whose release date has not arrived yet"
                  : "Titles that are already out"
              }
            />
          ))}
          {releaseCounts.unknown > 0 && (
            <span
              data-testid={`${prefix}-release-unknown`}
              title="No release date and no year on these rows, so they only show while both boxes are clear"
              className={`chip ${BIG_CHIP} flex items-center`}
            >
              No date <span className="font-mono opacity-70">{releaseCounts.unknown}</span>
            </span>
          )}
        </div>
      </div>

      <div>
        <p className="font-mono text-sm uppercase tracking-wider text-[#8C7F6D] mb-2.5">Media type</p>
        <div className="flex flex-wrap gap-2">
          {TYPE_CHECKS.map(({ key, label }) => (
            <CheckChip
              key={key}
              testid={`${prefix}-type-${key}`}
              on={types.has(key)}
              label={label}
              count={typeCounts[key] ?? 0}
              onToggle={() => onToggleType(key)}
              title={`Keep ${label.toLowerCase()}`}
            />
          ))}
        </div>
      </div>

      <p className="text-sm text-slate-500">
        Leave a row clear to keep everything in it. Tick <span className="text-[#F6EFE4]">Upcoming titles</span> on its
        own to see every upcoming title whatever the media type.
      </p>
    </div>
  );
}
