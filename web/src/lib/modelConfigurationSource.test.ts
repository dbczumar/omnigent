import { describe, expect, it } from "vitest";

import { modelConfigurationSourceRows } from "@/lib/modelConfigurationSource";

describe("modelConfigurationSourceRows", () => {
  it.each([
    [
      { kind: "databricks", label: "Workspace", name: "production-west", host: "ws.example.com" },
      "Databricks · production-west",
    ],
    [
      { kind: "gateway", label: "AI Gateway", name: "production", host: "gw.example.com" },
      "AI Gateway · production",
    ],
    [
      { kind: "key", label: "API key", name: "anthropic", host: "api.anthropic.com" },
      "API key · anthropic",
    ],
  ])("uses one identifying detail for %#", (source, value) => {
    expect(modelConfigurationSourceRows(source)).toEqual([{ label: "Connection", value }]);
  });
});
