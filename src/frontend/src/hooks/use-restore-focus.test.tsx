import { act, useEffect, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useRestoreFocus } from "./use-restore-focus";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

const Surface = ({ grabFocus = false }: { grabFocus?: boolean }) => {
    useRestoreFocus();
    useEffect(() => {
        if (grabFocus) document.getElementById("inside")!.focus();
    }, [grabFocus]);
    return (
        <div id="surface">
            <button type="button" id="inside" />
        </div>
    );
};

/** An opener owning a surface, plus a bystander that may claim the focus. */
const Host = ({ open, grabFocus, focusElsewhereOnClose = false }: { open: boolean; grabFocus?: boolean; focusElsewhereOnClose?: boolean }) => {
    const [wasOpen, setWasOpen] = useState(open);
    if (wasOpen !== open) setWasOpen(open);
    useEffect(() => {
        if (!open && focusElsewhereOnClose) document.getElementById("elsewhere")!.focus();
    }, [open, focusElsewhereOnClose]);
    return (
        <>
            <button type="button" id="opener" />
            <button type="button" id="elsewhere" />
            {open && <Surface grabFocus={grabFocus} />}
        </>
    );
};

const render = (props: React.ComponentProps<typeof Host>) =>
    act(() => {
        root.render(<Host {...props} />);
    });

const flushMicrotasks = () => act(() => Promise.resolve());

const byId = (id: string) => document.getElementById(id)!;

beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(() => {
    act(() => {
        root.unmount();
    });
    document.body.innerHTML = "";
});

describe("useRestoreFocus", () => {
    it("gives the focus back to the opener once the surface is gone", async () => {
        render({ open: false });
        byId("opener").focus();
        render({ open: true, grabFocus: true });
        expect(document.activeElement).toBe(byId("inside"));

        render({ open: false, grabFocus: true });
        await flushMicrotasks();

        expect(document.activeElement).toBe(byId("opener"));
    });

    it("leaves the focus alone when something else claimed it on close", async () => {
        render({ open: false });
        byId("opener").focus();
        render({ open: true, grabFocus: true });

        render({ open: false, grabFocus: true, focusElsewhereOnClose: true });
        await flushMicrotasks();

        expect(document.activeElement).toBe(byId("elsewhere"));
    });

    it("does nothing when nothing had the focus at mount (page load)", async () => {
        (document.activeElement as HTMLElement | null)?.blur();
        render({ open: true });
        render({ open: false });
        await flushMicrotasks();

        expect(document.activeElement).toBe(document.body);
    });
});
