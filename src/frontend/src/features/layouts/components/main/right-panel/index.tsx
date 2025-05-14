import { useResponsive } from "../hooks/useResponsive";
import clsx from "clsx";
import { PropsWithChildren } from "react";

export type RightPanelProps = {
  isOpen?: boolean;
};
export const RightPanel = ({
  children,
  isOpen,
}: PropsWithChildren<RightPanelProps>) => {
  const { isDesktop } = useResponsive();

  if (!isDesktop) {
    return (
      <div className={clsx("c__right-panel__mobile", { open: isOpen })}>
        {children}
      </div>
    );
  }

  return (
    <div className={clsx("c__right-panel", { open: isOpen })}>{children}</div>
  );
};
