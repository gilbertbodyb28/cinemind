import { ChevronDown, Cpu } from "lucide-react";
import { MODELS } from "@/lib/models";

export default function ModelPicker({ value, onChange, disabled, testid = "model-picker-select", compact = false }) {
  return (
    <label data-testid={`${testid}-wrap`} className={`relative inline-flex items-center gap-2 glass rounded-full pl-3 pr-8 ${compact ? "py-2" : "py-2.5"} text-sm cursor-pointer hover:border-[rgba(216,178,106,0.4)] transition-colors ${disabled ? "opacity-50 pointer-events-none" : ""}`}>
      <Cpu className="w-3.5 h-3.5 text-[#D8B26A] shrink-0" />
      <select
        data-testid={testid}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="appearance-none bg-transparent outline-none font-medium text-[#F6EFE4] pr-1 cursor-pointer"
      >
        {MODELS.map((m) => {
          const text = `${m.label} · ${m.hint}`;
          return <option key={m.key} value={m.key} className="bg-[#17130F] text-[#F6EFE4]" label={text}>{text}</option>;
        })}
      </select>
      <ChevronDown className="w-3.5 h-3.5 text-[#8C7F6D] absolute right-3 pointer-events-none" />
    </label>
  );
}
