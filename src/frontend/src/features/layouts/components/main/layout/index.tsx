import { PropsWithChildren, useEffect, useRef, useState } from "react";
import { Header } from "../header";
import {
  Panel,
  Group,
  Separator,
  useDefaultLayout,
} from "react-resizable-panels";
import { DropdownMenuOption, LeftPanel, useResponsive } from "@gouvfr-lasuite/ui-kit";
import { useControllableState } from "../hooks/useControllableState";
import { Toaster } from "@/features/ui/components/toaster";
import clsx from "clsx";
export type MainLayoutProps = {
  icon?: React.ReactNode;
  leftPanelContent?: React.ReactNode;
  rightHeaderContent?: React.ReactNode;
  languages?: DropdownMenuOption[];
  onToggleRightPanel?: () => void;
  enableResize?: boolean;
  rightPanelIsOpen?: boolean;
  hideLeftPanelOnDesktop?: boolean;
  isLeftPanelOpen?: boolean;
  setIsLeftPanelOpen?: (isLeftPanelOpen: boolean) => void;
  hideSearch?: boolean;
};

/**
 * This component is a copy of the MainLayout component from our ui-kit.
 *
 * @TODO: Remove this and update the ui-kit to be able to render panels without header or
 * add props to fully override the header.
 */
export const AppLayout = ({
  icon,
  children,
  hideLeftPanelOnDesktop = false,
  leftPanelContent,
  enableResize = false,
  hideSearch = false,
  ...props
}: PropsWithChildren<MainLayoutProps>) => {
  const [isLeftPanelOpen, setIsLeftPanelOpen] = useControllableState(
    false,
    props.isLeftPanelOpen,
    props.setIsLeftPanelOpen
  );
  const { defaultLayout, onLayoutChange } = useDefaultLayout({
    groupId: "main",
    storage: localStorage,
  });

  const { isDesktop } = useResponsive();
  const [isResizing, setIsResizing] = useState(false);
  const resizeTimeoutRef = useRef<number>(undefined);

  // We need to have two different states for the left panel, we want to always keep the
  // left panel mounted on mobile in order to show the animation when it opens or closes, instead
  // of abruptly disappearing when closing the panel.
  // On desktop, we want to hide the left panel when the prop is set to true, so we need to
  // completely unmount it as it will never be visible.
  const mountLeftPanel = isDesktop ? !hideLeftPanelOnDesktop : true;
  const showLeftPanel = isDesktop ? !hideLeftPanelOnDesktop : isLeftPanelOpen;

  const [minPanelSize, setMinPanelSize] = useState(
    calculateDefaultSize(300, isDesktop)
  );
  const [maxPanelSize, setMaxPanelSize] = useState(
    calculateDefaultSize(450, isDesktop)
  );

  const onTogglePanel = () => {
    setIsLeftPanelOpen(!isLeftPanelOpen);
  };

  useEffect(() => {
    const updatePanelSize = () => {
      const min = Math.round(calculateDefaultSize(300, isDesktop));
      const max = Math.round(
        Math.min(calculateDefaultSize(450, isDesktop), 40)
      );

      setMinPanelSize(isDesktop ? min : 0);
      if (enableResize) {
        setMaxPanelSize(max);
      } else {
        setMaxPanelSize(min);
      }
    };

    updatePanelSize();
    window.addEventListener("resize", updatePanelSize);

    return () => {
      window.removeEventListener("resize", updatePanelSize);
    };
  }, [isDesktop, enableResize]);

  // Disable transitions during window resize to prevent panels from being visible
  useEffect(() => {
    const handleResizeStart = () => {
      setIsResizing(true);
      if (resizeTimeoutRef.current) {
        clearTimeout(resizeTimeoutRef.current);
      }
      resizeTimeoutRef.current = window.setTimeout(() => {
        setIsResizing(false);
      }, 150);
    };

    window.addEventListener("resize", handleResizeStart);

    return () => {
      window.removeEventListener("resize", handleResizeStart);

      if (resizeTimeoutRef.current) {
        clearTimeout(resizeTimeoutRef.current);
      }
    };
  }, []);

  return (
    <div className={clsx("c__main-layout", isResizing && "c__main-layout--resizing")}>
      <div className="c__main-layout__header">
        <Header
          onTogglePanel={onTogglePanel}
          isPanelOpen={isLeftPanelOpen}
          leftIcon={icon}
          hideSearch={hideSearch}
        />
      </div>
      <main className="c__main-layout__content">
        <Group defaultLayout={defaultLayout} onLayoutChange={onLayoutChange} orientation="horizontal" style={{ flex: 1 }}>
          {mountLeftPanel && (
            <>
              <Panel
                id="panel-main-left"
                defaultSize={isDesktop ? `${minPanelSize}%` : "0"}
                minSize={isDesktop ? `${minPanelSize}%` : "0"}
                maxSize={`${maxPanelSize}%`}
              >
                <LeftPanel isOpen={showLeftPanel}>{leftPanelContent}</LeftPanel>
              </Panel>
              {isDesktop && (
                <Separator className="panel__resize-handle" />
              )}
            </>
          )}
          <Panel id="panel-main-right">
            {children}
          </Panel>
        </Group>
      </main>
      <Toaster />
    </div>
  );
};

const calculateDefaultSize = (targetWidth: number, isDesktop: boolean) => {
  if (!isDesktop) {
    return 0;
  }

  const windowWidth = window.innerWidth;

  return (targetWidth / windowWidth) * 100;
};
