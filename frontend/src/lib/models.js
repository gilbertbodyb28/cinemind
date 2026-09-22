export const MODELS = [
  { key: "qwen-suggestarr", label: "Qwen2.5 7B", hint: "Ollama" },
];

export const DEFAULT_MODEL = "qwen-suggestarr";

export const modelLabel = (key) => MODELS.find((m) => m.key === key)?.label || key || "Ollama";

export const providerLabel = (provider, model) => {
  if (provider === "demo" || provider === "fallback") return "Demo";
  const name = model || DEFAULT_MODEL;
  return `Ollama · ${name}`;
};

export const notifyUsage = () => window.dispatchEvent(new Event("cinemind:usage"));
