import { describe, expect, it } from "vitest";
import {
    computeRange,
    computeToggle,
    pruneSelection,
    resolveAnchorIndex,
} from "./thread-selection-core";

const threads = ['t1', 't2', 't3', 't4', 't5'].map((id) => ({ id }));

describe('computeToggle', () => {
    it('adds an unselected thread without affecting the others', () => {
        const next = computeToggle(new Set(['t1', 't2']), 't4');
        expect(next).toEqual(new Set(['t1', 't2', 't4']));
    });

    it('removes a selected thread without affecting the others', () => {
        const next = computeToggle(new Set(['t1', 't2', 't4']), 't2');
        expect(next).toEqual(new Set(['t1', 't4']));
    });

    it('does not mutate the previous selection', () => {
        const prev = new Set(['t1']);
        computeToggle(prev, 't2');
        expect(prev).toEqual(new Set(['t1']));
    });

    it('selects a thread when the selection is empty', () => {
        expect(computeToggle(new Set(), 't3')).toEqual(new Set(['t3']));
    });

    it('empties the selection when toggling off the last thread', () => {
        expect(computeToggle(new Set(['t3']), 't3')).toEqual(new Set());
    });
});

describe('resolveAnchorIndex', () => {
    it('uses the current anchor when it is still in the list', () => {
        expect(resolveAnchorIndex(threads, 4, 't2', 't3', 't1')).toBe(1);
    });

    it('falls back to the fallback anchor when the anchor is gone', () => {
        expect(resolveAnchorIndex(threads, 4, 'gone', 't3', 't1')).toBe(2);
    });

    it('seeds from the open thread when no anchor is usable', () => {
        expect(resolveAnchorIndex(threads, 4, null, undefined, 't1')).toBe(0);
    });

    it('anchors on the target itself as a last resort', () => {
        expect(resolveAnchorIndex(threads, 3, null)).toBe(3);
        expect(resolveAnchorIndex(threads, 3, 'gone', 'gone-too', 'gone-as-well')).toBe(3);
    });
});

describe('computeRange', () => {
    it('selects the inclusive range between anchor and target', () => {
        expect(computeRange(threads, 1, 3)).toEqual(new Set(['t2', 't3', 't4']));
    });

    it('handles a reversed range (target above anchor)', () => {
        expect(computeRange(threads, 3, 1)).toEqual(new Set(['t2', 't3', 't4']));
    });

    it('selects a single thread when anchor and target match', () => {
        expect(computeRange(threads, 2, 2)).toEqual(new Set(['t3']));
    });
});

describe('pruneSelection', () => {
    it('drops ids that left the thread list', () => {
        const pruned = pruneSelection(new Set(['t1', 'gone', 't3']), threads);
        expect(pruned).toEqual(new Set(['t1', 't3']));
    });

    it('returns the same reference when nothing changed', () => {
        const prev = new Set(['t1', 't3']);
        expect(pruneSelection(prev, threads)).toBe(prev);
    });

    it('returns the same reference when the selection is empty', () => {
        const prev = new Set<string>();
        expect(pruneSelection(prev, [])).toBe(prev);
    });

    it('prunes to empty when no selected thread remains', () => {
        expect(pruneSelection(new Set(['gone']), threads)).toEqual(new Set());
    });
});
