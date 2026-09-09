import { afterEach, describe, expect, it } from "vitest";

import { isFocusMoveWithinToolbar } from "./toolbar-focus";

const mount = (html: string) => {
    document.body.innerHTML = html;
};

afterEach(() => {
    document.body.innerHTML = "";
});

describe("isFocusMoveWithinToolbar", () => {
    it("is true for a move between two controls of the same toolbar", () => {
        mount(`
            <form>
              <div class="bn-toolbar">
                <button id="a"></button>
                <div class="mantine-Menu-dropdown"><button id="b"></button></div>
              </div>
            </form>
        `);
        expect(
            isFocusMoveWithinToolbar({
                target: document.getElementById("a"),
                relatedTarget: document.getElementById("b"),
            }),
        ).toBe(true);
    });

    it("is false when the focus enters or leaves the toolbar", () => {
        mount(`
            <form>
              <div class="ProseMirror" id="editor" contenteditable="true"></div>
              <div class="bn-toolbar"><button id="a"></button></div>
              <input id="subject" />
            </form>
        `);
        const a = document.getElementById("a");
        expect(
            isFocusMoveWithinToolbar({ target: document.getElementById("editor"), relatedTarget: a }),
        ).toBe(false);
        expect(
            isFocusMoveWithinToolbar({ target: a, relatedTarget: document.getElementById("subject") }),
        ).toBe(false);
        // Focus dropped entirely (click on a non-focusable area).
        expect(isFocusMoveWithinToolbar({ target: a, relatedTarget: null })).toBe(false);
    });

    it("is false between two different toolbars", () => {
        mount(`
            <div class="bn-toolbar"><button id="a"></button></div>
            <div class="bn-toolbar"><button id="b"></button></div>
        `);
        expect(
            isFocusMoveWithinToolbar({
                target: document.getElementById("a"),
                relatedTarget: document.getElementById("b"),
            }),
        ).toBe(false);
    });
});
