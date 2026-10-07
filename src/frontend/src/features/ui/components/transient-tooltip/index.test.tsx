import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TransientTooltip } from ".";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const Harness = ({ onHide }: { onHide: () => void }) => {
    const [message, setMessage] = useState<string | null>("Refreshed");
    return (
        <TransientTooltip
            message={message}
            onHide={() => {
                onHide();
                setMessage(null);
            }}
        >
            <button type="button" />
        </TransientTooltip>
    );
};

const pressEscape = () => {
    act(() => {
        document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
};

describe("TransientTooltip", () => {
    let container: HTMLDivElement;
    let root: Root;
    const onHide = vi.fn();

    beforeEach(() => {
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
        act(() => root.render(<Harness onHide={onHide} />));
    });

    afterEach(() => {
        act(() => root.unmount());
        container.remove();
        onHide.mockReset();
    });

    it("closes on Escape and stops swallowing it once closed", () => {
        const escapesSeen = vi.fn();
        document.addEventListener("keydown", escapesSeen);

        pressEscape();
        expect(onHide).toHaveBeenCalledTimes(1);
        expect(escapesSeen).not.toHaveBeenCalled();

        pressEscape();
        expect(escapesSeen).toHaveBeenCalledTimes(1);

        document.removeEventListener("keydown", escapesSeen);
    });

    it("stays open when its trigger loses focus", () => {
        const button = container.querySelector("button")!;

        act(() => {
            button.focus();
            button.blur();
        });

        expect(onHide).not.toHaveBeenCalled();
        expect(document.querySelector(".c__tooltip")).not.toBeNull();
    });
});
