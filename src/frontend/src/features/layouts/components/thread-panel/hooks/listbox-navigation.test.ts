import { describe, expect, it } from "vitest";
import { getNextFocusId, isListboxNavKey } from "./listbox-navigation";

const threads = ['t1', 't2', 't3'].map((id) => ({ id }));

describe('getNextFocusId', () => {
    it('moves focus down', () => {
        expect(getNextFocusId(threads, 't1', 'ArrowDown')).toBe('t2');
    });

    it('moves focus up', () => {
        expect(getNextFocusId(threads, 't3', 'ArrowUp')).toBe('t2');
    });

    it('clamps at the bottom edge', () => {
        expect(getNextFocusId(threads, 't3', 'ArrowDown')).toBe('t3');
    });

    it('clamps at the top edge', () => {
        expect(getNextFocusId(threads, 't1', 'ArrowUp')).toBe('t1');
    });

    it('jumps to the first thread on Home', () => {
        expect(getNextFocusId(threads, 't3', 'Home')).toBe('t1');
    });

    it('jumps to the last loaded thread on End', () => {
        expect(getNextFocusId(threads, 't1', 'End')).toBe('t3');
    });

    it('falls back to the first thread when no thread is focused', () => {
        expect(getNextFocusId(threads, null, 'ArrowDown')).toBe('t1');
    });

    it('falls back to the first thread when the focused thread left the list', () => {
        expect(getNextFocusId(threads, 'gone', 'ArrowUp')).toBe('t1');
    });

    it('returns null on an empty list', () => {
        expect(getNextFocusId([], 't1', 'ArrowDown')).toBeNull();
        expect(getNextFocusId([], null, 'Home')).toBeNull();
    });
});

describe('isListboxNavKey', () => {
    it('accepts navigation keys', () => {
        expect(isListboxNavKey('ArrowUp')).toBe(true);
        expect(isListboxNavKey('ArrowDown')).toBe(true);
        expect(isListboxNavKey('Home')).toBe(true);
        expect(isListboxNavKey('End')).toBe(true);
    });

    it('rejects other keys', () => {
        expect(isListboxNavKey('Enter')).toBe(false);
        expect(isListboxNavKey(' ')).toBe(false);
        expect(isListboxNavKey('a')).toBe(false);
    });
});
