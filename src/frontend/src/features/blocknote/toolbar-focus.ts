const TOOLBAR_SELECTOR = ".bn-toolbar";

type FocusMove = {
    target: EventTarget | null;
    relatedTarget: EventTarget | null;
};

const closestToolbar = (node: EventTarget | null) =>
    node instanceof Element ? node.closest(TOOLBAR_SELECTOR) : null;

/**
 * True when a blur is a focus move between two controls of the same BlockNote
 * toolbar (Tab between buttons, a select opening its menu…).
 *
 * Such a move carries nothing new to persist, so a form that saves on blur
 * should skip it — and must: a save re-renders the form, and the Mantine
 * toolbar of @blocknote/mantine snaps the focus back to its first button on
 * every re-render while the focus is inside it (its focus trap sees a fresh
 * merged ref each render). Skipping the save keeps the keyboard navigation
 * usable until the upstream fix lands.
 */
export const isFocusMoveWithinToolbar = ({ target, relatedTarget }: FocusMove) => {
    const toolbar = closestToolbar(target);
    return toolbar !== null && toolbar === closestToolbar(relatedTarget);
};
