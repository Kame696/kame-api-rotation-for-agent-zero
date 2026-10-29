// Agent Zero's model search, reduced to what the fallback editor calls
export const store = {
  chatProviders: [{ value: "openai", label: "OpenAI" }, { value: "google", label: "Google" }],
  ensureLoaded: async () => {},
  searchModelsDetailed: async (provider, query) => {
    globalThis.__searches = globalThis.__searches || [];
    globalThis.__searches.push([provider, query]);
    const all = provider === "openai" ? ["gpt-5.4-nano", "gpt-5.4", "o4-mini"] : ["gemini/gemini-3.5-flash"];
    return { models: all, provider, source: "litellm_registry", error: "" };
  },
  groupResults(models, query) {
    const q = (query || "").trim().toLowerCase();
    if (!q) return { matched: [], rest: models };
    return { matched: models.filter((m) => m.toLowerCase().includes(q)), rest: models.filter((m) => !m.toLowerCase().includes(q)) };
  },
};
