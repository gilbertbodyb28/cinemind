export const MODELS = [
  { key: "qwen3:14b", label: "Qwen3 14B", hint: "Ollama" },
];

export const DEFAULT_MODEL = "qwen3:14b";

export const modelLabel = (key) => MODELS.find((m) => m.key === key)?.label || key || "Ollama";

export const providerLabel = (provider, model) => {
  if (provider === "demo" || provider === "fallback") return "Demo";
  const name = model || DEFAULT_MODEL;
  return `Ollama · ${name}`;
};

export const notifyUsage = () => window.dispatchEvent(new Event("cinemind:usage"));
