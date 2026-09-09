import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
    Modal,
    ModalProvider,
    ModalSize,
} from "@gouvfr-lasuite/cunningham-react";

import { useCompactTabDrillDown } from "./use-compact-tab-drill-down";

// Opts into React's act() support, which the shared setup does not enable since
// most tests here render to static markup rather than mounting.
declare global {
    var IS_REACT_ACT_ENVIRONMENT: boolean;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const IMPORTS_CONTENT = "imports content";

// The workaround leans on Cunningham's internals (a click on the active tab
// button is the only thing that advances its drill-down) and on react-modal
// attaching its portal after our ref runs. Both are load-bearing and invisible
// from the props, so the modal is rendered for real rather than stubbed.
const TabModalFixture = ({ activeTab }: { activeTab?: string }) => {
    const drillDownRef = useCompactTabDrillDown(Boolean(activeTab));

    return (
        <ModalProvider>
            <Modal
                isOpen
                onClose={() => {}}
                aria-label="settings"
                size={ModalSize.LARGE}
                variant="tab"
                sidebarTitle={<div ref={drillDownRef} />}
                tabs={[
                    { id: "general", label: "General", content: <p /> },
                    {
                        id: "imports",
                        label: "Imports",
                        content: <p>{IMPORTS_CONTENT}</p>,
                    },
                ]}
                activeTab={activeTab ?? ""}
                onTabChange={() => {}}
            />
        </ModalProvider>
    );
};

const mockViewportWidth = (isCompact: boolean) => {
    vi.stubGlobal(
        "matchMedia",
        vi.fn(() => ({
            matches: isCompact,
            addEventListener: () => {},
            removeEventListener: () => {},
        })),
    );
};

let container: HTMLDivElement;
let root: Root;

const render = async (element: React.ReactElement) => {
    await act(async () => {
        root.render(element);
    });
    // The click is queued in a microtask so it lands after react-modal has
    // attached its portal; let that microtask and the resulting render flush.
    await act(async () => {});
};

const isShowingContent = () =>
    document
        .querySelector(".c__modal__tab-layout")
        ?.classList.contains("c__modal__tab-layout--show-content") ?? false;

beforeEach(() => {
    container = document.createElement("div");
    // The class react-modal hides from screen readers while a modal is open;
    // its provider asserts the element exists.
    container.className = "c__app";
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
});

describe("useCompactTabDrillDown", () => {
    it("opens the compact modal on the preselected tab's content", async () => {
        mockViewportWidth(true);

        await render(<TabModalFixture activeTab="imports" />);

        expect(isShowingContent()).toBe(true);
        expect(
            document.querySelector('[role="tabpanel"]')?.textContent,
        ).toBe(IMPORTS_CONTENT);
    });

    it("leaves the compact modal on the sidebar when no tab is preselected", async () => {
        mockViewportWidth(true);

        await render(<TabModalFixture />);

        expect(isShowingContent()).toBe(false);
    });

    it("does not touch the wide layout, which already shows both panes", async () => {
        mockViewportWidth(false);

        await render(<TabModalFixture activeTab="imports" />);

        expect(isShowingContent()).toBe(false);
    });
});
