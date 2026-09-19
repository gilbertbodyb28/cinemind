import { cn } from "@/lib/utils";

const tones = {
  rose: "chip chip-rose",
  cyan: "chip chip-cyan",
  violet: "chip chip-rose",
  amber: "chip chip-amber",
  emerald: "chip chip-emerald",
  neutral: "chip",
};

export default function Badge({ children, tone = "neutral", className, ...props }) {
  return (
    <span className={cn(tones[tone] || tones.neutral, className)} {...props}>
      {children}
    </span>
  );
}
