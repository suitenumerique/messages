/* eslint-disable @typescript-eslint/no-explicit-any */
// ES2023 "change array by copy" methods are missing from Chromium <= 109 —
// the last Chrome/Edge versions available on Windows 7, still in use in some
// collectivités. BlockNote's paste pipeline calls `marks.toReversed()` on
// every pasted text node (after having already called `preventDefault()`),
// so without these shims pasting into the composer is a silent no-op there.
// See https://github.com/suitenumerique/messages/issues — "Paste does nothing
// in the message composer on Chromium <= 109".

const defineIfMissing = (name: string, value: (...args: any[]) => unknown) => {
    if (!(name in Array.prototype)) {
        // Non-enumerable, like the native methods, so for..in stays clean.
        Object.defineProperty(Array.prototype, name, {
            value,
            writable: true,
            configurable: true,
        });
    }
};

defineIfMissing("toReversed", function toReversed(this: unknown[]) {
    return [...this].reverse();
});

defineIfMissing("toSorted", function toSorted(
    this: unknown[],
    compareFn?: (a: unknown, b: unknown) => number,
) {
    return [...this].sort(compareFn);
});

defineIfMissing("toSpliced", function toSpliced(
    this: unknown[],
    start: number,
    deleteCount?: number,
    ...items: unknown[]
) {
    const copy = [...this];
    // splice(start) and splice(start, undefined) differ: the one-argument
    // form deletes through to the end, and toSpliced mirrors that.
    if (arguments.length === 1) {
        copy.splice(start);
    } else {
        copy.splice(start, deleteCount as number, ...items);
    }
    return copy;
});

defineIfMissing("with", function with_(this: unknown[], index: number, value: unknown) {
    const actualIndex = index < 0 ? this.length + index : index;
    if (actualIndex < 0 || actualIndex >= this.length) {
        throw new RangeError(`Invalid index : ${index}`);
    }
    const copy = [...this];
    copy[actualIndex] = value;
    return copy;
});

export {};
