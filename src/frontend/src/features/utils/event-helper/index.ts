import type { MouseEvent } from "react";

class EventHelper {
    /**
     * Cancels the `contextmenu` Android raises on a long press, so a link the
     * user only means to tap does not offer "open in new tab". A mouse
     * right-click keeps its menu.
     * Browsers that raise it as a plain `MouseEvent` give no pointer type; on
     * those, the device is taken as touch only when nothing on it can hover.
     * @param event - the `contextmenu` event to filter.
     */
    static preventTouchContextMenu(event: MouseEvent) {
        const { nativeEvent } = event;
        const pointerType = 'pointerType' in nativeEvent ? nativeEvent.pointerType : '';
        const isTouch = pointerType
            ? pointerType === 'touch'
            : window.matchMedia('(any-hover: none)').matches;
        if (isTouch) {
            event.preventDefault();
        }
    }

    /**
     * Tells whether the user is on an Apple device, whose keyboards mostly
     * lack a forward delete key: their ⌫ key raises `Backspace`. iPadOS
     * presents itself as a Mac.
     * @returns true on macOS, iOS and iPadOS.
     */
    static isApplePlatform(): boolean {
        return /Macintosh|Mac OS X|iPhone|iPad|iPod/.test(navigator.userAgent);
    }

    /**
     * Hides the image a browser draws under the cursor during a drag, for a
     * preview of the page to follow the cursor instead. Safari drew nothing
     * of a copy of the dragged element built on the drag start, and takes an
     * image only once loaded: an empty element in the page works everywhere.
     * @param dataTransfer - the `dataTransfer` of the `dragstart` event.
     */
    static hideNativeDragImage(dataTransfer: DataTransfer) {
        const empty = document.createElement('div');
        empty.style.cssText = 'position:absolute;top:-1000px;left:-1000px;width:1px;height:1px;';
        document.body.appendChild(empty);
        dataTransfer.setDragImage(empty, 0, 0);
        setTimeout(() => empty.remove(), 0);
    }
}

export default EventHelper;
