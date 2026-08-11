/**
 * Claude-native model picker options: Claude Code's version-agnostic
 * aliases (not pinned IDs), so `/model opus` resolves to the latest
 * installed Opus — the list never drifts when a version retires, and the
 * ucode `ANTHROPIC_DEFAULT_*_MODEL` env pins redirect the same alias.
 *
 * Lives in a leaf module (no React / store imports) so both the picker UI
 * (`ChatPage`) and the store (`chatStore`) can read it without a circular
 * import.
 */
export const CLAUDE_NATIVE_MODELS = [
  // Ordered by capability tier, most powerful first.
  { id: "fable", label: "Fable" },
  { id: "opus", label: "Opus" },
  // Version-agnostic on purpose: which Sonnet the alias lands on is the
  // harness's call, and the live catalog's display name supersedes this
  // label wherever one has arrived. A pinned version here paints a wrong
  // claim during the pre-catalog window (it read "Sonnet 4.6" while the
  // catalog resolves the alias to Sonnet 5).
  { id: "sonnet", label: "Sonnet" },
  // Newer Sonnet, offered as an explicit opt-in via Claude Code's one
  // custom /model slot (ANTHROPIC_CUSTOM_MODEL_OPTION) — not a family
  // alias, and it does NOT change the default "sonnet" binding above.
  { id: "sonnet_5", label: "Sonnet 5" },
  { id: "haiku", label: "Haiku" },
] as const;

/**
 * Is `model` something a Claude Code (claude-native) session can actually
 * run — i.e. a Claude model rather than a foreign harness's id?
 *
 * Accepts the version-agnostic aliases (`fable` / `opus` / `sonnet` /
 * `haiku`) and any fully-qualified Anthropic id (anything containing
 * `claude`, e.g. `claude-fable-5`, `anthropic/claude-opus-4-8`,
 * `databricks-claude-sonnet-4-6`). Rejects everything else — notably the
 * Codex / OpenAI defaults (`gpt-5.4`, `gpt-5.4-mini`, …) that leak into the
 * cross-harness global picker selection.
 *
 * This is the guard for the sticky-model handoff: the auto-apply only
 * pushes a model onto a claude-native session when it passes this check,
 * so a `gpt-*` id picked up from a Codex session can never be handed to
 * Claude Code (which would launch `claude --model gpt-5.4` and fail).
 *
 * @param model - A model id / alias, or null/undefined.
 * @returns True only for a Claude-compatible model.
 */
export function isClaudeNativeModel(model: string | null | undefined): boolean {
  if (model == null) return false;
  const id = model.toLowerCase();
  return CLAUDE_NATIVE_MODELS.some((m) => m.id === id) || id.includes("claude");
}
