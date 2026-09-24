/** Fallback only. The real list comes from the user's own Ollama host
 *  (`GET /connections/ollama/models`) - a hard-coded set goes stale the moment
 *  someone pulls or removes a model, and picking a name the host does not have
 *  produces a job that fails at generate time. */
export const DEFAULT_MODEL = "gemma4:12b-it-qat";

export const modelLabel = (key) => (key === "demo" ? "Demo picks" : key || "Ollama");

export const providerLabel = (provider, model) => {
  if (provider === "demo" || provider === "fallback") return "Demo";
  const name = model || DEFAULT_MODEL;
  return `Ollama · ${name}`;
};

export const notifyUsage = () => window.dispatchEvent(new Event("cinemind:usage"));
