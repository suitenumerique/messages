import { describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import type { IconSvgProps } from "@gouvfr-lasuite/ui-kit";

import { APP_ICON_NAMES, Icon } from "./index";

type MockMaterialIconProps = {
    name: string;
    role?: string;
    "aria-label"?: string;
    "aria-hidden"?: boolean;
};

vi.mock("@gouvfr-lasuite/ui-kit", () => ({
    Icon: (props: MockMaterialIconProps) => (
        <span
            data-material-icon={props.name}
            role={props.role}
            aria-label={props["aria-label"]}
            aria-hidden={props["aria-hidden"]}
        />
    ),
    IconSize: {
        X_SMALL: "xsmall",
        SMALL: "small",
        MEDIUM: "medium",
        LARGE: "large",
        X_LARGE: "xlarge",
    },
    IconType: { OUTLINED: "outlined", FILLED: "filled" },
    getIconSize: () => 24,
}));

// Eager + raw so the assets themselves can be asserted on, without touching
// the lazy loading used by the component.
const rawAssets = import.meta.glob<string>("./assets/*.svg", {
    query: "?raw",
    import: "default",
    eager: true,
});

const assetNames = Object.keys(rawAssets)
    .map((path) => path.replace("./assets/", "").replace(".svg", ""))
    .sort();

(
    globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

const renderInDom = async (element: React.JSX.Element) => {
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => {
        root.render(element);
    });
    // The lazy SVG import resolves through real module I/O, so give it a few
    // macrotasks rather than a single microtask flush.
    for (let i = 0; i < 20 && !container.querySelector("svg"); i += 1) {
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 10));
        });
    }
    return container;
};

describe("Icon", () => {
    it("keeps APP_ICON_NAMES in sync with the SVG assets", () => {
        expect(assetNames).toEqual([...APP_ICON_NAMES].sort());
    });

    it("only uses customizable colors in the SVG assets", () => {
        // The only fixed color allowed is tag-fill's decorative light sheen.
        for (const [path, markup] of Object.entries(rawAssets)) {
            const fixedColors = (
                markup.match(/(?:fill|stroke)="(?!currentColor|none)[^"]*"/g) ??
                []
            ).filter((attr) => !attr.includes("#F6F8FA"));
            expect(fixedColors, path).toEqual([]);
        }
    });

    it("renders an app icon as inline SVG once loaded", async () => {
        const container = await renderInDom(
            <Icon name="reply" aria-label="Répondre" size={16} />,
        );
        const wrapper = container.querySelector(".messages-icon");
        expect(wrapper?.getAttribute("role")).toBe("img");
        expect(wrapper?.getAttribute("style")).toContain("font-size: 16px");
        expect(wrapper?.querySelector("svg")).toBeTruthy();
    });

    it("sets no inline size without a size prop, so a container can drive it", async () => {
        const container = await renderInDom(<Icon name="reply" />);
        const wrapper = container.querySelector(".messages-icon");
        expect(wrapper?.getAttribute("style") ?? "").not.toContain(
            "font-size",
        );
    });

    it("is hidden from assistive tech when it has no label", async () => {
        const container = await renderInDom(<Icon name="reply" />);
        const wrapper = container.querySelector(".messages-icon");
        expect(wrapper?.getAttribute("aria-hidden")).toBe("true");
        expect(wrapper?.getAttribute("role")).toBeNull();
    });

    it("falls back to the ui-kit Material icon for unknown names", () => {
        const html = renderToStaticMarkup(<Icon name="close" />);
        expect(html).toContain('data-material-icon="close"');
        expect(html).toContain('aria-hidden="true"');
        expect(html).not.toContain("messages-icon");
    });

    it("labels the Material fallback when an aria-label is given", () => {
        const html = renderToStaticMarkup(
            <Icon name="close" aria-label="Fermer" />,
        );
        expect(html).toContain('role="img"');
        expect(html).toContain('aria-label="Fermer"');
        expect(html).not.toContain("aria-hidden");
    });

    it("proxies ui-kit SVG icon components through the icon prop", () => {
        const UIKitSvgIcon = (props: IconSvgProps) => (
            <svg
                className={props.className as string | undefined}
                width={props.width as string | undefined}
                data-size={props.size}
                data-color={props.color}
                role={props.role as string | undefined}
                aria-label={props["aria-label"] as string | undefined}
                aria-hidden={props["aria-hidden"] as boolean | undefined}
            />
        );

        const decorative = renderToStaticMarkup(
            <Icon icon={UIKitSvgIcon} size={32} color="red" />,
        );
        expect(decorative).toContain('data-size="32"');
        expect(decorative).toContain('data-color="red"');
        expect(decorative).toContain('aria-hidden="true"');
        expect(decorative).not.toContain('width="1em"');

        const labelled = renderToStaticMarkup(
            <Icon icon={UIKitSvgIcon} aria-label="Déplier" />,
        );
        expect(labelled).toContain('role="img"');
        expect(labelled).toContain('aria-label="Déplier"');
        expect(labelled).not.toContain("aria-hidden");
    });

    it("makes unsized ui-kit SVG icons follow the font-size context", () => {
        const UIKitSvgIcon = (props: IconSvgProps) => (
            <svg
                className={props.className as string | undefined}
                width={props.width as string | undefined}
                height={props.height as string | undefined}
            />
        );
        const html = renderToStaticMarkup(<Icon icon={UIKitSvgIcon} />);
        expect(html).toContain('class="messages-icon--svg"');
        expect(html).toContain('width="1em"');
        expect(html).toContain('height="1em"');
    });
});
