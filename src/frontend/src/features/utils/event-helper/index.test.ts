import type { MouseEvent } from 'react';
import { vi, describe, it, expect, afterEach } from 'vitest';
import EventHelper from './index';

describe('EventHelper', () => {
    describe('preventTouchContextMenu', () => {
        const buildEvent = (nativeEvent: object) => ({
            nativeEvent,
            preventDefault: vi.fn(),
        }) as unknown as MouseEvent;

        const mockAnyHoverNone = (matches: boolean) => {
            vi.stubGlobal('matchMedia', vi.fn(() => ({ matches })));
        };

        afterEach(() => {
            vi.unstubAllGlobals();
        });

        it('should prevent the context menu raised by a touch long press', () => {
            const event = buildEvent({ pointerType: 'touch' });
            EventHelper.preventTouchContextMenu(event);
            expect(event.preventDefault).toHaveBeenCalled();
        });

        it('should keep the context menu of a mouse right-click', () => {
            mockAnyHoverNone(true);
            const event = buildEvent({ pointerType: 'mouse' });
            EventHelper.preventTouchContextMenu(event);
            expect(event.preventDefault).not.toHaveBeenCalled();
        });

        it('should prevent the context menu without pointer type on a touch-only device', () => {
            mockAnyHoverNone(true);
            const event = buildEvent({});
            EventHelper.preventTouchContextMenu(event);
            expect(event.preventDefault).toHaveBeenCalled();
            expect(window.matchMedia).toHaveBeenCalledWith('(any-hover: none)');
        });

        it('should prevent the context menu with an empty pointer type on a touch-only device', () => {
            mockAnyHoverNone(true);
            const event = buildEvent({ pointerType: '' });
            EventHelper.preventTouchContextMenu(event);
            expect(event.preventDefault).toHaveBeenCalled();
        });

        it('should keep the context menu without pointer type on a device that can hover', () => {
            mockAnyHoverNone(false);
            const event = buildEvent({});
            EventHelper.preventTouchContextMenu(event);
            expect(event.preventDefault).not.toHaveBeenCalled();
        });
    });
});
