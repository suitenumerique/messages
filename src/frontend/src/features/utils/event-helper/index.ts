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
}

export default EventHelper;
