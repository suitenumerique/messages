import { act, useRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useFocusTrap } from "./use-focus-trap";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

const Harness = ({ active }: { active: boolean }) => {
    const ref = useRef<HTMLElement>(null);
    useFocusTrap(ref, active);
    return (
        <>
            <button type="button" id="before" />
            <section ref={ref} id="trap" tabIndex={-1}>
                <button type="button" id="first" />
                <input id="middle" />
                <div hidden>
                    <button type="button" id="hidden" />
                </div>
                <button type="button" id="last" />
            </section>
            <button type="button" id="after" />
        </>
    );
};

const render = (active: boolean) =>
    act(() => {
        root.render(<Harness active={active} />);
    });

const byId = (id: string) => document.getElementById(id)!;

const pressTab = (shiftKey = false) => {
    const event = new KeyboardEvent("keydown", { key: "Tab", shiftKey, bubbles: true, cancelable: true });
    act(() => {
        document.activeElement!.dispatchEvent(event);
    });
    return event;
};

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

describe("useFocusTrap", () => {
    it("wraps Tab from the last tabbable to the first, skipping hidden subtrees", () => {
        render(true);
        byId("last").focus();

        const event = pressTab();

        expect(event.defaultPrevented).toBe(true);
        expect(document.activeElement).toBe(byId("first"));
    });

    it("wraps Shift+Tab from the first tabbable, or from the root itself, to the last", () => {
        render(true);
        byId("first").focus();
        expect(pressTab(true).defaultPrevented).toBe(true);
        expect(document.activeElement).toBe(byId("last"));

        byId("trap").focus();
        expect(pressTab(true).defaultPrevented).toBe(true);
        expect(document.activeElement).toBe(byId("last"));
    });

    it("leaves Tab alone in the middle: the browser moves the focus", () => {
        render(true);
        byId("middle").focus();

        expect(pressTab().defaultPrevented).toBe(false);
        expect(pressTab(true).defaultPrevented).toBe(false);
    });

    it("does nothing while inactive", () => {
        render(false);
        byId("last").focus();

        expect(pressTab().defaultPrevented).toBe(false);
        expect(document.activeElement).toBe(byId("last"));
    });
});
