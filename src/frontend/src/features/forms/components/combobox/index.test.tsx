import { describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { CunninghamProvider } from "@gouvfr-lasuite/ui-components";
import { ComboBox } from ".";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// Translations load over HTTP, which suspends `useTranslation` forever in
// jsdom; the labels themselves are not what is under test here.
vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key }),
}));

const WARNING = "Accented characters before the @: delivery is not guaranteed.";

type RenderOptions = {
    getItemWarning?: (value: string) => string | undefined;
    className?: string;
    variant?: "floating" | "inline";
    actions?: React.ReactNode;
};

const render = async ({ getItemWarning, className, variant, actions }: RenderOptions = {}) => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    await act(async () => {
        createRoot(container).render(
            <CunninghamProvider>
                <ComboBox
                    label="To"
                    options={[]}
                    value={["josé@example.com", "john@example.com"]}
                    getItemWarning={getItemWarning}
                    className={className}
                    variant={variant}
                    actions={actions}
                />
            </CunninghamProvider>
        );
    });
    return container;
};

describe("ComboBox chip warnings", () => {
    it("marks only the chips the warning callback flags", async () => {
        const container = await render({ getItemWarning: (value) => (value.startsWith("josé") ? WARNING : undefined) });

        const chips = Array.from(container.querySelectorAll(".c__combobox__chip"));
        expect(chips).toHaveLength(2);
        expect(chips[0].classList.contains("c__combobox__chip--warning")).toBe(true);
        expect(chips[1].classList.contains("c__combobox__chip--warning")).toBe(false);

        // The warning is exposed to assistive technologies, not only as a
        // hover tooltip: touch devices have no hover.
        const marker = chips[0].querySelector('[role="img"]');
        expect(marker?.getAttribute("aria-label")).toBe(WARNING);
        expect(chips[1].querySelector('[role="img"]')).toBeNull();
    });

    it("keeps its root classes when given a className", async () => {
        const container = await render({ className: "extra" });

        const root = container.querySelector(".c__combobox");
        expect(root).not.toBeNull();
        expect(root?.classList.contains("extra")).toBe(true);
    });

    it("puts the label in the inline grid column and renders the actions", async () => {
        const container = await render({ variant: "inline", actions: <button type="button" data-testid="cc-toggle" /> });

        const root = container.querySelector(".c__combobox");
        expect(root?.classList.contains("c__field--inline")).toBe(true);
        // The label must be a direct child of the field to land in the
        // left column of the ui-kit inline grid, and stay associated with
        // the input.
        const label = root?.querySelector(":scope > label");
        expect(label?.textContent).toBe("To");
        const input = root?.querySelector("input:not([type=hidden])");
        expect(label?.getAttribute("for")).toBe(input?.getAttribute("id"));
        expect(root?.querySelector(".labelled-box")).toBeNull();
        expect(root?.querySelector(".c__select__inner__actions [data-testid=cc-toggle]")).not.toBeNull();
    });

    it("renders plain chips without a warning callback", async () => {
        const container = await render();

        expect(container.querySelectorAll(".c__combobox__chip")).toHaveLength(2);
        expect(container.querySelector(".c__combobox__chip--warning")).toBeNull();
    });
});
