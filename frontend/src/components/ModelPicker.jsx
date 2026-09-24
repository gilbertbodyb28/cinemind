import { useCallback, useEffect, useState } from "react";
import { Check, ChevronDown, Cpu, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { modelLabel } from "@/lib/models";

/** Bytes as the picker shows them: one decimal, so 7.2 GB stays readable. */
const sizeLabel = (bytes) =>
  typeof bytes === "number" && bytes > 0 ? `${(bytes / 1e9).toFixed(1)} GB` : "";

/** "gemma4:12b-it-qat · 12B · Q4_K_M · 7.2 GB" - whatever the host actually reports. */
const optionLabel = (m) =>
  [m.name, m.parameter_size, m.quantization, sizeLabel(m.size)].filter(Boolean).join(" · ");

/**
 * Live model picker for the user's own Ollama host.
 *
 * The list is whatever `ollama pull` has put on that machine, not a hard-coded
 * set: picking a name the host does not have produces a job that fails at
 * generate time. A selection is local until Save writes it to the account, so
 * trying a model for one run never silently changes the stored default.
 */
export default function ModelPicker({
  value,
  onChange,
  disabled,
  testid = "model-picker-select",
  compact = false,
  onSaved,
}) {
  const [models, setModels] = useState([]);
  const [state, setState] = useState("loading"); // loading | ok | error
  const [message, setMessage] = useState("");
  const [saved, setSaved] = useState(value);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setState("loading");
    try {
      const r = await api.get("/connections/ollama/models");
      setModels(r.data.models || []);
      setMessage(r.data.message || "");
      setState(r.data.ok ? "ok" : "error");
      if (r.data.current) setSaved(r.data.current);
    } catch {
      setModels([]);
      setMessage("Could not reach CineMind");
      setState("error");
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    setSaving(true);
    try {
      await api.put("/connections", { ollama_model: value });
      setSaved(value);
      toast.success(`Saved ${modelLabel(value)} as your model`);
      onSaved?.(value);
    } catch {
      toast.error("Could not save the model");
    } finally {
      setSaving(false);
    }
  };

  // A stored model the host no longer has must stay selectable, or the picker
  // would silently show a different model than the one the account will use.
  const known = models.some((m) => m.name === value);
  const dirty = value !== saved;
  const pad = compact ? "py-2" : "py-2.5";

  return (
    <div className="flex items-center gap-2">
      <label
        data-testid={`${testid}-wrap`}
        className={`relative inline-flex items-center gap-2 glass rounded-full pl-3 pr-8 ${pad} text-sm cursor-pointer hover:border-[rgba(216,178,106,0.4)] transition-colors ${disabled ? "opacity-50 pointer-events-none" : ""}`}
      >
        {state === "loading"
          ? <Loader2 className="w-3.5 h-3.5 text-[#D8B26A] shrink-0 animate-spin" />
          : <Cpu className="w-3.5 h-3.5 text-[#D8B26A] shrink-0" />}
        <select
          data-testid={testid}
          value={value || ""}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
          className="appearance-none bg-transparent outline-none font-medium text-[#F6EFE4] pr-1 cursor-pointer max-w-[22rem] truncate"
        >
          {!known && value && (
            <option value={value} className="bg-[#17130F] text-[#F6EFE4]">
              {value} · not on this host
            </option>
          )}
          {models.map((m) => (
            <option key={m.name} value={m.name} className="bg-[#17130F] text-[#F6EFE4]">
              {optionLabel(m)}
            </option>
          ))}
          {state === "error" && !value && (
            <option value="" className="bg-[#17130F] text-[#F6EFE4]">No models found</option>
          )}
        </select>
        <ChevronDown className="w-3.5 h-3.5 text-[#8C7F6D] absolute right-3 pointer-events-none" />
      </label>

      <button
        type="button"
        data-testid={`${testid}-refresh`}
        onClick={load}
        disabled={disabled || state === "loading"}
        title={message || "Reload the model list"}
        className={`glass rounded-full ${pad} px-3 text-sm grid place-items-center hover:border-[rgba(216,178,106,0.4)] transition-colors disabled:opacity-50`}
      >
        <RefreshCw className={`w-3.5 h-3.5 text-[#8C7F6D] ${state === "loading" ? "animate-spin" : ""}`} />
      </button>

      <button
        type="button"
        data-testid={`${testid}-save`}
        onClick={save}
        disabled={disabled || saving || !value || !dirty}
        title={dirty ? `Save ${value} to your account` : "This model is already saved"}
        className={`glass-strong rounded-full ${pad} px-4 text-sm font-medium inline-flex items-center gap-2 transition-shadow hover:brutal-shadow-rose disabled:opacity-50 disabled:hover:shadow-none`}
      >
        {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
        {saving ? "Saving…" : dirty ? "Save" : "Saved"}
      </button>

      {state === "error" && (
        <span data-testid={`${testid}-status`} className="chip chip-rose">Ollama offline</span>
      )}
    </div>
  );
}
