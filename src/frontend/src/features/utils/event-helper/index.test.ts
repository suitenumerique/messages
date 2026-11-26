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

    describe('isApplePlatform', () => {
        afterEach(() => {
            vi.restoreAllMocks();
        });

        it.each([
            ['macOS', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.5 Safari/605.1.15', true],
            ['iOS', 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148', true],
            ['Windows', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36', false],
            ['Linux', 'Mozilla/5.0 (X11; Linux x86_64; rv:145.0) Gecko/20100101 Firefox/145.0', false],
            ['Android', 'Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Mobile Safari/537.36', false],
        ])('should tell %s apart', (_platform, userAgent, expected) => {
            vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue(userAgent);
            expect(EventHelper.isApplePlatform()).toBe(expected);
        });
    });

    describe('hideNativeDragImage', () => {
        afterEach(() => {
            vi.useRealTimers();
        });

        it('should give an empty element of the page as the drag image, then remove it', () => {
            vi.useFakeTimers();
            const setDragImage = vi.fn<(image: Element, x: number, y: number) => void>();

            EventHelper.hideNativeDragImage({ setDragImage } as unknown as DataTransfer);

            const [image] = setDragImage.mock.calls[0];
            expect(image.isConnected).toBe(true);
            expect(image.childNodes).toHaveLength(0);
            vi.runAllTimers();
            expect(image.isConnected).toBe(false);
        });
    });
});
