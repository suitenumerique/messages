import { describe, expect, it } from "vitest";

import {
  clampToDirection,
  resolveDragEnd,
  updateVelocity,
} from "./use-drag-gesture";

describe("clampToDirection", () => {
  it("keeps only downward/rightward movement when positive", () => {
    expect(clampToDirection(40, "positive")).toBe(40);
    expect(clampToDirection(-40, "positive")).toBe(0);
  });

  it("keeps only upward/leftward movement when negative", () => {
    expect(clampToDirection(-40, "negative")).toBe(-40);
    expect(clampToDirection(40, "negative")).toBe(0);
  });

  it("keeps both signs when unconstrained", () => {
    expect(clampToDirection(40, undefined)).toBe(40);
    expect(clampToDirection(-40, undefined)).toBe(-40);
  });
});

describe("updateVelocity", () => {
  it("weighs the newest sample most", () => {
    // 30px in 16ms ≈ 1.875 px/ms instantaneous.
    const velocity = updateVelocity(0, 30, 16);
    expect(velocity).toBeCloseTo(1.5, 1);
    // A stop (no movement) quickly decays the running value.
    expect(updateVelocity(velocity, 0, 16)).toBeCloseTo(0.3, 1);
  });

  it("ignores zero-elapsed samples instead of dividing by zero", () => {
    expect(updateVelocity(1.2, 30, 0)).toBe(1.2);
  });
});

describe("resolveDragEnd", () => {
  const base = {
    direction: "positive" as const,
    commitDistance: 100,
    commitVelocity: 0.5,
  };

  it("commits past the distance threshold regardless of speed", () => {
    expect(resolveDragEnd({ ...base, offset: 120, velocity: 0 })).toBe("commit");
  });

  it("cancels a slow short drag", () => {
    expect(resolveDragEnd({ ...base, offset: 40, velocity: 0.1 })).toBe("cancel");
  });

  it("commits a flick even under the distance threshold", () => {
    expect(resolveDragEnd({ ...base, offset: 40, velocity: 1.2 })).toBe("commit");
  });

  it("only accepts flicks along the configured direction", () => {
    expect(resolveDragEnd({ ...base, offset: 0, velocity: -2 })).toBe("cancel");
    expect(
      resolveDragEnd({
        ...base,
        direction: "negative",
        offset: -40,
        velocity: -1.2,
      }),
    ).toBe("commit");
  });

  it("accepts flicks both ways when unconstrained", () => {
    expect(
      resolveDragEnd({ ...base, direction: undefined, offset: 0, velocity: -2 }),
    ).toBe("commit");
  });
});
