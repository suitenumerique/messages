import {
    ComponentProps,
    Components,
    ComponentsContext,
    useBlockNoteEditor,
    useComponentsContext,
} from "@blocknote/react";
import { PropsWithChildren, useMemo } from "react";
import { createPortal } from "react-dom";
import { HiChevronDown } from "react-icons/hi";

type MenuDropdownProps = ComponentProps["Generic"]["Menu"]["Dropdown"];
type MenuItemProps = ComponentProps["Generic"]["Menu"]["Item"];
type ToolbarSelectProps = ComponentProps["FormattingToolbar"]["Select"];

/**
 * The toolbar components with their menus rendered in the editor's portal
 * element rather than inside the toolbar.
 *
 * Built on the components of the enclosing context, so nothing of the
 * `@blocknote/mantine` implementation is copied: the dropdown is the upstream
 * one, only moved through a React portal, and the select is composed from the
 * generic menu primitives (the upstream select bypasses them, hence this
 * recomposition).
 */
const usePortalComponents = (): Components => {
    const components = useComponentsContext()!;
    const editor = useBlockNoteEditor();

    return useMemo(() => {
        const { Root: MenuRoot, Trigger: MenuTrigger, Dropdown, Item: MenuItem } = components.Generic.Menu;
        const ToolbarButton = components.FormattingToolbar.Button;

        const PortalDropdown = (props: MenuDropdownProps) => {
            // A sub menu is anchored to an item of its parent dropdown, which
            // is already in the portal: it stays where it is.
            if (props.sub) return <Dropdown {...props} />;
            return createPortal(<Dropdown {...props} />, editor.portalElement);
        };

        const Select = ({ className, items, isDisabled }: ToolbarSelectProps) => {
            const selectedItem = items.find((item) => item.isSelected);

            if (!selectedItem) return null;

            return (
                <MenuRoot>
                    <MenuTrigger>
                        <ToolbarButton label={selectedItem.text} isDisabled={isDisabled}>
                            <span className="toolbar-select__value">
                                {selectedItem.icon}
                                {selectedItem.text}
                                <HiChevronDown />
                            </span>
                        </ToolbarButton>
                    </MenuTrigger>
                    <PortalDropdown className={className}>
                        {items.map((item) => {
                            // The generic item contract has no disabled state;
                            // the Mantine implementation forwards unknown props
                            // to Mantine's Menu.Item, which has one.
                            const itemProps: MenuItemProps & { disabled?: boolean } = {
                                icon: item.icon,
                                checked: item.isSelected,
                                onClick: item.onClick,
                                disabled: item.isDisabled,
                            };
                            return (
                                <MenuItem key={item.text} {...itemProps}>
                                    {item.text}
                                </MenuItem>
                            );
                        })}
                    </PortalDropdown>
                </MenuRoot>
            );
        };

        return {
            ...components,
            Generic: {
                ...components.Generic,
                Menu: { ...components.Generic.Menu, Dropdown: PortalDropdown },
            },
            FormattingToolbar: { ...components.FormattingToolbar, Select },
        };
    }, [components, editor]);
};

/**
 * Renders the menus of the enclosed toolbar (block type and signature selects,
 * color menu…) in the editor's portal element instead of inside the toolbar.
 *
 * `@blocknote/mantine` renders every menu with `withinPortal={false}`, so the
 * dropdown lives in the toolbar's DOM. With the toolbar pinned at the bottom
 * of the message form's scrolling surface, that dropdown is painted under the
 * sticky footer and stretches the scroller instead of showing: the selects
 * are unusable. The portal element is a body-level node carrying the editor
 * theme classes: there, the dropdown escapes the scroller and flips against
 * the viewport.
 *
 * @TODO: remove once BlockNote ships a `portalElement` prop on its Menu and
 * ToolbarSelect adapters, and drops the toolbar focus trap, so the menus
 * follow `portalElements` natively and `isFocusMoveWithinToolbar` can go too.
 * See https://github.com/TypeCellOS/BlockNote/pull/3052
 */
export const ToolbarMenusPortal = ({ children }: PropsWithChildren) => {
    const components = usePortalComponents();

    return (
        <ComponentsContext.Provider value={components}>
            {children}
        </ComponentsContext.Provider>
    );
};
