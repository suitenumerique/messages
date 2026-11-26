import EventHelper from "@/features/utils/event-helper";

export const CHIP_PRESSING_CLASS = "c__combobox__chip--pressing";
export const CHIP_DRAGGING_CLASS = "c__combobox__chip--dragging";

type Point = { x: number, y: number };

/**
 * The copy of a dragged chip following the pointer, grabbed at `grab`:
 * lifted, without its remove button, out of the hit testing and the
 * accessibility tree.
 */
export const createChipDragGhost = (chip: Element, grab: Point) => {
    const rect = chip.getBoundingClientRect();
    const ghost = chip.cloneNode(true) as HTMLElement;
    ghost.querySelectorAll("[id]").forEach((element) => element.removeAttribute("id"));
    ghost.querySelector(".c__combobox__chip__clear")?.remove();
    ghost.classList.remove(CHIP_PRESSING_CLASS, CHIP_DRAGGING_CLASS);
    ghost.classList.add("c__combobox__chip--lifted", "c__combobox__chip-ghost");
    ghost.setAttribute("aria-hidden", "true");
    const offset = { x: grab.x - rect.left, y: grab.y - rect.top };
    // `translate` rather than `transform`: the scale of the lift applies to a
    // `transform`, which would stretch the whole way from the window corner.
    const place = (position: Point) => {
        ghost.style.translate = `${position.x - offset.x}px ${position.y - offset.y}px`;
    };
    place(grab);
    document.body.appendChild(ghost);
    return { place, remove: () => ghost.remove() };
};

/**
 * Makes a mouse drag carry the copy a finger drags, as the browser's own image
 * of the chip is not drawn everywhere (Safari drew none): that image is hidden
 * and the copy follows the drag instead, until it ends.
 * @param chip - the chip the drag starts from.
 * @param event - the `dragstart` event.
 */
export const followMouseChipDrag = (chip: Element, event: DragEvent) => {
    if (!event.dataTransfer) return;
    EventHelper.hideNativeDragImage(event.dataTransfer);
    const ghost = createChipDragGhost(chip, { x: event.clientX, y: event.clientY });

    const onDragOver = (over: DragEvent) => {
        // Accepted anywhere: a drop refused is animated back by the browser
        // before the drag ends, the copy frozen meanwhile. Only the fields of
        // the chip's group act on it.
        over.preventDefault();
        ghost.place({ x: over.clientX, y: over.clientY });
    };
    const stop = () => {
        window.removeEventListener("dragover", onDragOver, true);
        window.removeEventListener("drop", stop, true);
        window.removeEventListener("dragend", stop, true);
        ghost.remove();
    };
    window.addEventListener("dragover", onDragOver, true);
    // The drop, or the end of a drag released out of the page.
    window.addEventListener("drop", stop, true);
    window.addEventListener("dragend", stop, true);
};
