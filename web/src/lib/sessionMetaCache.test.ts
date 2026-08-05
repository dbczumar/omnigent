import { afterEach, describe, expect, it, vi } from "vitest";
import {
  evictSessionMeta,
  readSessionMeta,
  writeSessionMeta,
  type SessionMeta,
} from "./sessionMetaCache";

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

function meta(patch: Partial<SessionMeta> = {}): SessionMeta {
  return {
    gitBranch: "feature/x",
    llmModel: "system.ai.claude-sonnet-5",
    sessionModelOverride: null,
    sessionHarness: "claude",
    nativeVendorOwnsModel: false,
    codexModelOptions: [{ id: "opus", displayName: "Opus 4.10" }],
    ...patch,
  };
}

describe("sessionMetaCache", () => {
  it("returns null for a session it has never seen", () => {
    // A miss must read as "nothing known" so the caller paints blanks rather
    // than some other session's branch/model.
    expect(readSessionMeta("conv_unknown")).toBeNull();
  });

  it("round-trips one session's metadata and keeps sessions independent", () => {
    writeSessionMeta("conv_a", meta({ gitBranch: "feature/a" }));
    writeSessionMeta("conv_b", meta({ gitBranch: "feature/b", sessionHarness: "codex" }));

    expect(readSessionMeta("conv_a")?.gitBranch).toBe("feature/a");
    expect(readSessionMeta("conv_a")?.sessionHarness).toBe("claude");
    expect(readSessionMeta("conv_b")?.gitBranch).toBe("feature/b");
    expect(readSessionMeta("conv_a")?.codexModelOptions).toEqual([
      { id: "opus", displayName: "Opus 4.10" },
    ]);
  });

  it("drops an entry that would paint nothing", () => {
    // An all-empty entry reads the same as a miss, so it must not hold a slot —
    // and re-writing one must clear a previously useful entry rather than
    // leaving stale values behind.
    writeSessionMeta("conv_empty", meta());
    writeSessionMeta(
      "conv_empty",
      meta({
        gitBranch: null,
        llmModel: null,
        sessionHarness: null,
        codexModelOptions: [],
      }),
    );
    expect(readSessionMeta("conv_empty")).toBeNull();
  });

  it("evicts a deleted session", () => {
    writeSessionMeta("conv_gone", meta());
    evictSessionMeta("conv_gone");
    expect(readSessionMeta("conv_gone")).toBeNull();
  });

  it("prunes the oldest-touched sessions past the cap", () => {
    // 50 is the cap; writing 60 must keep the newest 50 and drop the rest, so
    // the store can't grow without bound.
    for (let i = 0; i < 60; i++) writeSessionMeta(`conv_${i}`, meta({ gitBranch: `branch/${i}` }));
    expect(readSessionMeta("conv_0")).toBeNull();
    expect(readSessionMeta("conv_9")).toBeNull();
    expect(readSessionMeta("conv_10")?.gitBranch).toBe("branch/10");
    expect(readSessionMeta("conv_59")?.gitBranch).toBe("branch/59");
  });

  it("re-touching a session protects it from pruning", () => {
    for (let i = 0; i < 50; i++) writeSessionMeta(`conv_${i}`, meta());
    writeSessionMeta("conv_0", meta({ gitBranch: "branch/touched" }));
    writeSessionMeta("conv_new", meta());
    // conv_0 was refreshed, so conv_1 (now the oldest) is evicted instead.
    expect(readSessionMeta("conv_0")?.gitBranch).toBe("branch/touched");
    expect(readSessionMeta("conv_1")).toBeNull();
  });

  it("survives corrupt or hand-edited storage instead of throwing", () => {
    localStorage.setItem("omnigent:session-meta.v1", "not json");
    expect(readSessionMeta("conv_a")).toBeNull();

    // Wrong-typed fields degrade to empty rather than reaching the store as
    // e.g. a number where the composer expects a branch string.
    localStorage.setItem(
      "omnigent:session-meta.v1",
      JSON.stringify([
        {
          id: "conv_a",
          meta: { gitBranch: 42, codexModelOptions: [{ noId: true }, { id: "ok" }] },
        },
        { id: 7, meta: {} },
      ]),
    );
    const stored = readSessionMeta("conv_a");
    expect(stored?.gitBranch).toBeNull();
    expect(stored?.nativeVendorOwnsModel).toBe(false);
    expect(stored?.codexModelOptions).toEqual([{ id: "ok" }]);
  });

  it("never throws when storage is inaccessible", () => {
    // Private-mode / quota failures surface as throws from the Storage API;
    // a broken cache must not break session switching.
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota exceeded");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("access denied");
    });
    expect(() => writeSessionMeta("conv_a", meta())).not.toThrow();
    expect(readSessionMeta("conv_a")).toBeNull();
  });
});
