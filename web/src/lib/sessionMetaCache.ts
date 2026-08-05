// Per-session composer metadata — the worktree branch and the model identity —
// persisted so switching back to a session repaints its tray and model label
// from last-known values instead of blanking until the snapshot lands. The
// snapshot overwrites these a moment later; this only fills the gap.

import type { NativeModelOption } from "@/lib/types";

export interface SessionMeta {
  /** Worktree branch shown in the composer tray. */
  gitBranch: string | null;
  /** Model the session launched with. */
  llmModel: string | null;
  /** Model pinned on this session, if any. */
  sessionModelOverride: string | null;
  /** Harness identity behind the composer's label and gear tooltip. */
  sessionHarness: string | null;
  /** Whether the vendor TUI owns the model (no Omnigent-visible pick). */
  nativeVendorOwnsModel: boolean;
  /** The session's own model catalog, which the label resolves ids against. */
  codexModelOptions: NativeModelOption[];
}

const STORAGE_KEY = "omnigent:session-meta.v1";
// Cap stored sessions so the store can't grow without bound. The
// least-recently-touched entries (front of the array) are pruned first.
const MAX_SESSIONS = 50;

/**
 * One persisted session entry. The store is an ordered array (not a keyed
 * object) so recency ordering survives serialization regardless of the id
 * format — a plain `Record` reorders integer-like keys, breaking both the
 * "touch = move to end" refresh and the oldest-first pruning. Same shape as
 * `sessionWorkspaceState`, for the same reason.
 */
interface StoredEntry {
  id: string;
  meta: SessionMeta;
}

type Store = StoredEntry[];

function nullableString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

/**
 * Keep catalog rows that carry the one field every consumer needs (`id`);
 * the rest are optional to downstream code, so they ride along untouched.
 */
function sanitizeModelOptions(value: unknown): NativeModelOption[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (option): option is NativeModelOption =>
      typeof option === "object" &&
      option !== null &&
      typeof (option as { id?: unknown }).id === "string",
  );
}

function sanitize(entry: unknown): SessionMeta {
  if (typeof entry !== "object" || entry === null) return emptyMeta();
  const record = entry as Record<string, unknown>;
  return {
    gitBranch: nullableString(record.gitBranch),
    llmModel: nullableString(record.llmModel),
    sessionModelOverride: nullableString(record.sessionModelOverride),
    sessionHarness: nullableString(record.sessionHarness),
    nativeVendorOwnsModel: record.nativeVendorOwnsModel === true,
    codexModelOptions: sanitizeModelOptions(record.codexModelOptions),
  };
}

function emptyMeta(): SessionMeta {
  return {
    gitBranch: null,
    llmModel: null,
    sessionModelOverride: null,
    sessionHarness: null,
    nativeVendorOwnsModel: false,
    codexModelOptions: [],
  };
}

/** Whether an entry would paint nothing — not worth a slot in the store. */
function isEmpty(meta: SessionMeta): boolean {
  return (
    meta.gitBranch === null &&
    meta.llmModel === null &&
    meta.sessionModelOverride === null &&
    meta.sessionHarness === null &&
    meta.codexModelOptions.length === 0
  );
}

function readStore(): Store {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const store: Store = [];
    for (const item of parsed) {
      if (typeof item !== "object" || item === null) continue;
      const record = item as Record<string, unknown>;
      if (typeof record.id !== "string") continue;
      store.push({ id: record.id, meta: sanitize(record.meta) });
    }
    return store;
  } catch {
    return [];
  }
}

function writeStore(store: Store): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
  } catch {
    // Storage quota/access errors must not break session switching.
  }
}

/**
 * Read one session's cached composer metadata, or `null` when not cached.
 *
 * Strictly keyed by session id: a miss returns `null` so the caller paints
 * nothing, never another session's values.
 */
export function readSessionMeta(conversationId: string): SessionMeta | null {
  return readStore().find((entry) => entry.id === conversationId)?.meta ?? null;
}

/** Persist one session's composer metadata, replacing any previous entry. */
export function writeSessionMeta(conversationId: string, meta: SessionMeta): void {
  const store = readStore();
  const existingIdx = store.findIndex((entry) => entry.id === conversationId);
  if (existingIdx >= 0) store.splice(existingIdx, 1);
  // An all-empty entry reads the same as a miss, so drop it rather than let it
  // hold a slot (mirrors writeTranscriptCache dropping an empty transcript).
  if (isEmpty(meta)) {
    if (existingIdx >= 0) writeStore(store);
    return;
  }
  // Re-append so the most-recently-touched session moves to the end; pruning
  // then evicts from the front (oldest-touched).
  store.push({ id: conversationId, meta });
  if (store.length > MAX_SESSIONS) {
    store.splice(0, store.length - MAX_SESSIONS);
  }
  writeStore(store);
}

/** Drop a deleted session's cached metadata. */
export function evictSessionMeta(conversationId: string): void {
  const store = readStore();
  const idx = store.findIndex((entry) => entry.id === conversationId);
  if (idx < 0) return;
  store.splice(idx, 1);
  writeStore(store);
}
