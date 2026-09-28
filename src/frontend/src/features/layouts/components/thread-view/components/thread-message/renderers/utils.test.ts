import { describe, it, expect } from "vitest";
import { getFirstSrcsetUrl } from "./utils";

describe("getFirstSrcsetUrl", () => {
  it.each([
    ["https://e.example/a.png 1x, https://e.example/b.png 2x", "https://e.example/a.png"],
    ["https://e.example/a.png, https://e.example/b.png 2x", "https://e.example/a.png"],
    ["  ,https://e.example/a.png 480w", "https://e.example/a.png"],
    ["https://e.example/a,b.png 1x", "https://e.example/a,b.png"],
    ["https://e.example/a.png", "https://e.example/a.png"],
  ])("extracts the first candidate of %s", (srcset, expected) => {
    expect(getFirstSrcsetUrl(srcset)).toBe(expected);
  });

  it.each(["", "   ", " , , "])("returns null for an empty srcset %j", (srcset) => {
    expect(getFirstSrcsetUrl(srcset)).toBeNull();
  });
});
