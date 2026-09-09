import { compareVersionCounts } from "./ota-version.mjs";

describe("compareVersionCounts", () => {
  it("orders hybrid ids by their leading count, ignoring the sha", () => {
    expect(compareVersionCounts("101-bbb", "100-aaa")).toBeGreaterThan(0);
    expect(compareVersionCounts("99-aaa", "100-bbb")).toBeLessThan(0);
    expect(compareVersionCounts("100-aaa", "100-bbb")).toBe(0);
  });

  it("compares counts numerically, not lexically", () => {
    expect(compareVersionCounts("1000-a", "999-b")).toBeGreaterThan(0);
  });

  it("has no opinion when either id carries no count", () => {
    expect(compareVersionCounts("a1b2c3d4", "100-aaa")).toBeNull();
    expect(compareVersionCounts("100-aaa", "builtin")).toBeNull();
    expect(compareVersionCounts("a1b2c3d4", "e5f6a7b8")).toBeNull();
    expect(compareVersionCounts(undefined, "100-aaa")).toBeNull();
  });
});
