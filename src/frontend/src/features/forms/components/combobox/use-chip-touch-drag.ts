import { TouchEvent, useEffect, useRef } from "react";
import { LongPressPosition, useLongPress } from "@/hooks/use-long-press";
import { CHIP_PRESSING_CLASS, createChipDragGhost } from "./chip-drag-preview";

/**
 * What the drop handlers read of a DataTransfer. Browsers keep the data of a
 * real one for themselves until the drop, while an emulated drag must tell
 * every field what it carries.
 */
const createDataTransfer = () => {
    const data = new Map<string, string>();
    return {
        get types() { return Array.from(data.keys()); },
        setData: (type: string, value: string) => { data.set(type, value); },
        getData: (type: string) => data.get(type) ?? "",
        effectAllowed: "uninitialized",
        dropEffect: "none",
    };
};
type EmulatedDataTransfer = ReturnType<typeof createDataTransfer>;

const EMULATED = "isEmulatedChipDrag";

/** Tells a drag emulated from a touch apart from one the browser started. */
export const isEmulatedChipDrag = (event: Event) => EMULATED in event;

const dispatchDragEvent = (
    target: Element,
    type: string,
    dataTransfer: EmulatedDataTransfer,
    { x, y }: LongPressPosition,
    relatedTarget: Element | null = null,
) => {
    const event = new Event(type, { bubbles: true, cancelable: true });
    Object.defineProperties(event, {
        dataTransfer: { value: dataTransfer },
        clientX: { value: x },
        clientY: { value: y },
        relatedTarget: { value: relatedTarget },
        [EMULATED]: { value: true },
    });
    target.dispatchEvent(event);
    return event;
};

/**
 * Lets a finger drag the chips of a combobox. Browsers start their own touch
 * drag on the hold alone, unevenly across them and the native app web views,
 * so the chips refuse it: a long press starts a drag emulated from the touch,
 * sending the drag events a mouse drag sends, to whatever lies under the
 * finger. Every drop target of the mouse drag thus works the same.
 */
export const useChipTouchDrag = ({ enabled }: { enabled: boolean }) => {
    const pressedChipRef = useRef<Element | null>(null);
    const stopRef = useRef<(() => void) | null>(null);

    // A drag still running when the field goes away must not leave its ghost.
    useEffect(() => () => stopRef.current?.(), []);

    const startDrag = (chip: Element, start: LongPressPosition): boolean => {
        const dataTransfer = createDataTransfer();
        if (dispatchDragEvent(chip, "dragstart", dataTransfer, start).defaultPrevented) return false;
        const ghost = createChipDragGhost(chip, start);
        let position = start;
        let target: Element | null = null;
        let accepted = false;

        const moveTo = (next: LongPressPosition) => {
            if (next.x === position.x && next.y === position.y && target) return;
            position = next;
            // Looks under the finger before moving the ghost, which would
            // make the browser compute the page once more within the frame.
            const under = document.elementFromPoint(position.x, position.y);
            if (under !== target) {
                if (target) dispatchDragEvent(target, "dragleave", dataTransfer, position, under);
                if (under) dispatchDragEvent(under, "dragenter", dataTransfer, position, target);
                target = under;
            }
            accepted = !!target && dispatchDragEvent(target, "dragover", dataTransfer, position).defaultPrevented;
            ghost.place(position);
        };
        // Handled once a frame, at the last position: each move of the finger
        // raises both a pointer and a touch event, sometimes more often than
        // the screen refreshes.
        let pending: LongPressPosition | null = null;
        let frame = 0;
        const flush = () => {
            cancelAnimationFrame(frame);
            frame = 0;
            if (pending) moveTo(pending);
            pending = null;
        };
        const scheduleMove = (next: LongPressPosition) => {
            pending = next;
            if (!frame) frame = requestAnimationFrame(flush);
        };
        const onPointerMove = (event: PointerEvent) => {
            if (event.pointerType === "touch") scheduleMove({ x: event.clientX, y: event.clientY });
        };
        const onTouchMove = (event: globalThis.TouchEvent) => {
            const touch = event.touches[0];
            if (!touch) return;
            if (event.cancelable) event.preventDefault();
            scheduleMove({ x: touch.clientX, y: touch.clientY });
        };

        const stop = (drop: boolean) => {
            window.removeEventListener("pointermove", onPointerMove, true);
            window.removeEventListener("touchmove", onTouchMove, true);
            window.removeEventListener("pointerup", onRelease, true);
            window.removeEventListener("touchend", onRelease, true);
            window.removeEventListener("pointercancel", onCancel, true);
            window.removeEventListener("touchcancel", onCancel, true);
            stopRef.current = null;
            // Where the finger was last, even within the frame.
            flush();
            ghost.remove();
            if (target && drop && accepted) {
                dispatchDragEvent(target, "drop", dataTransfer, position);
            } else if (target) {
                dispatchDragEvent(target, "dragleave", dataTransfer, position);
            }
            dispatchDragEvent(chip, "dragend", dataTransfer, position);
        };
        // Android sends no touchend after a long press, only a pointerup.
        const onRelease = () => stop(true);
        const onCancel = () => stop(false);

        window.addEventListener("pointermove", onPointerMove, true);
        window.addEventListener("touchmove", onTouchMove, { capture: true, passive: false });
        window.addEventListener("pointerup", onRelease, true);
        window.addEventListener("touchend", onRelease, true);
        window.addEventListener("pointercancel", onCancel, true);
        window.addEventListener("touchcancel", onCancel, true);
        stopRef.current = () => stop(false);
        // A drop right where the finger is.
        moveTo(start);
        return true;
    };

    const { handlers, isTouching, pressing } = useLongPress((position) => {
        const chip = pressedChipRef.current;
        if (!enabled || !chip) return false;
        return startDrag(chip, position) ? undefined : false;
    });

    // The chip shrinks while held, as a native element about to be lifted;
    // its copy grows from there once the drag starts.
    useEffect(() => {
        pressedChipRef.current?.classList.toggle(CHIP_PRESSING_CLASS, pressing);
    }, [pressing]);

    /** For the element holding the chips. */
    const chipTouchHandlers = {
        onTouchStart: (event: TouchEvent) => {
            const chip = (event.target as Element).closest(".c__combobox__chip[draggable='true']");
            pressedChipRef.current = chip;
            if (chip) handlers.onTouchStart(event);
        },
        onTouchMove: handlers.onTouchMove,
        onTouchEnd: handlers.onTouchEnd,
        onTouchCancel: handlers.onTouchCancel,
    };

    return { chipTouchHandlers, isTouchingChip: isTouching };
};
