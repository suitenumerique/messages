import { Button } from "@openfun/cunningham-react";
import clsx from "clsx";
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
  type?: "error" | "info";
  actions?: ToastAction[];
} & Partial<ToastContentProps>) => {
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
            color="primary-text"
            size="small"
            icon={action.icon && <span className="material-icons">{action.icon}</span>}
          >{action.showLabel || !action.icon && action.label}</Button>
        ))}
        {closeButton && (
          <Button
            onClick={closeToast}
            color="primary-text"
            size="small"
            icon={<span className="material-icons">close</span>}
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
