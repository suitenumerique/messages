import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon } from "@gouvfr-lasuite/ui-kit";
import clsx from "clsx";
import { useMemo } from "react";
import { Slide, ToastContainer, ToastContentProps, toast } from "react-toastify";

export const Toaster = () => {
  return <ToastContainer />;
};

type ToastAction = {
  label: string;
  showLabel?: boolean;
  icon?: string;
  onClick: () => void;
}

export const ToasterItem = ({
  children,
  closeToast,
  closeButton = true,
  className,
  actions = [],
  type = "info",
}: {
  children: React.ReactNode;
  closeButton?: boolean;
  className?: string;
  type?: "error" | "info" | "warning";
  actions?: ToastAction[];
} & Partial<ToastContentProps>) => {

  const buttonColor = useMemo(() => {
    switch (type) {
      case "error":
        return "error";
      case "warning":
        return "warning";
      default:
        return "brand";
    }
  }, [type]);
  return (
    <div
      className={clsx(
        "suite__toaster__item",
        "suite__toaster__item--" + type,
        className
      )}
    >
      <div className="suite__toaster__item__content">{children}</div>
      <div className="suite__toaster__item__actions">
        {actions.map((action) => (
          <Button
            key={action.label}
            aria-label={!action.showLabel ? action.label : undefined}
            onClick={action.onClick}
            color={buttonColor}
            variant="tertiary"
            size="small"
            icon={action.icon && <Icon name={action.icon} />}
          >{action.showLabel || !action.icon && action.label}</Button>
        ))}
        {closeButton && (
          <Button
            onClick={closeToast}
            color={buttonColor}
            variant="tertiary"
            size="small"
            icon={<Icon name="close" />}
          ></Button>
        )}
      </div>
    </div>
  );
};

export const addToast = (
  children: React.ReactNode,
  options: Parameters<typeof toast>[1] = {}
) => {
  return toast(children, {
    position: "bottom-left",
    closeButton: false,
    className: "suite__toaster__wrapper",
    autoClose: 5000,
    transition: Slide,
    hideProgressBar: true,
    ...options,
  });
};
