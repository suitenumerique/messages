import { Children, isValidElement, type ReactElement, type ReactNode } from "react";

import { withoutPrintAction } from "./without-print-action";

// Mirrors the kit's header node: a fragment holding plain buttons and the
// "more" menu element, the latter carrying the options array.
type MenuOption = { value?: string; label: string };
const Menu: (props: { options: MenuOption[] }) => null = () => null;

const header = (
    <>
        <button type="button" aria-label="download" />
        <button type="button" aria-label="info" />
        <Menu
            options={[
                { value: "download", label: "Download" },
                { value: "print", label: "Print" },
                { label: "Custom" },
            ]}
        />
    </>
);

const childrenOf = (node: ReactNode): ReactElement[] =>
    Children.toArray((node as ReactElement<{ children?: ReactNode }>).props.children).filter(isValidElement);

describe("withoutPrintAction", () => {
    it("removes only the print option from the menu element", () => {
        const children = childrenOf(withoutPrintAction(header));

        const menu = children.find((child) => child.type === Menu) as ReactElement<{ options: MenuOption[] }>;
        expect(menu.props.options).toEqual([
            { value: "download", label: "Download" },
            { label: "Custom" },
        ]);
    });

    it("passes the other header children through", () => {
        const children = childrenOf(withoutPrintAction(header));

        expect(children.filter((child) => child.type === "button")).toHaveLength(2);
    });

    it("returns an unexpected node untouched", () => {
        expect(withoutPrintAction("plain")).toBe("plain");
        expect(withoutPrintAction(null)).toBeNull();
    });
});
