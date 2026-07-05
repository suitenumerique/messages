/* eslint-disable @typescript-eslint/no-explicit-any */
// The test runtime (modern Node) already ships the ES2023 change-array-by-copy
// methods, so we delete them first to emulate Chromium <= 109 (last Chrome/Edge
// on Windows 7), then load the polyfill and check it restores them.

const METHODS = ["toReversed", "toSorted", "toSpliced", "with"] as const;

beforeAll(async () => {
    for (const method of METHODS) {
        delete (Array.prototype as any)[method];
    }
    await import("./polyfills");
});

describe("Array change-by-copy polyfills (Chromium <= 109)", () => {
    it.each(METHODS)("defines %s as non-enumerable", (method) => {
        expect(typeof (Array.prototype as any)[method]).toBe("function");
        expect(Object.keys([]).includes(method)).toBe(false);
        // for..in over arrays must not pick up the polyfill either
        const seen: string[] = [];
        for (const key in [1, 2]) seen.push(key);
        expect(seen).toEqual(["0", "1"]);
    });

    it("toReversed returns a reversed copy without mutating", () => {
        const marks = [1, 2, 3];
        expect(marks.toReversed()).toEqual([3, 2, 1]);
        expect(marks).toEqual([1, 2, 3]);
        expect([].toReversed()).toEqual([]);
    });

    it("toSorted returns a sorted copy without mutating", () => {
        const values = [3, 1, 2];
        expect(values.toSorted()).toEqual([1, 2, 3]);
        expect(values.toSorted((a, b) => b - a)).toEqual([3, 2, 1]);
        expect(values).toEqual([3, 1, 2]);
    });

    it("toSpliced returns a spliced copy without mutating", () => {
        const values = [1, 2, 3, 4];
        expect(values.toSpliced(1, 2, 9)).toEqual([1, 9, 4]);
        // single-argument form deletes through to the end, like splice(start)
        expect(values.toSpliced(2)).toEqual([1, 2]);
        expect(values).toEqual([1, 2, 3, 4]);
    });

    it("with returns a copy with one index replaced, supporting negatives", () => {
        const values = [1, 2, 3];
        expect(values.with(1, 9)).toEqual([1, 9, 3]);
        expect(values.with(-1, 9)).toEqual([1, 2, 9]);
        expect(values).toEqual([1, 2, 3]);
        expect(() => values.with(3, 9)).toThrow(RangeError);
    });
});
