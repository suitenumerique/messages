import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon } from "@gouvfr-lasuite/ui-kit";
import clsx from "clsx";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation();
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
            aria-label={!!action.showLabel ? undefined : action.label}
            onClick={action.onClick}
            color={buttonColor}
            variant="tertiary"
            size="small"
            icon={action.icon && <Icon name={action.icon} aria-hidden={true} />}
          >
            {(action.showLabel || !action.icon) && action.label}
          </Button>
        ))}
        {closeButton && (
          <Button
            onClick={closeToast}
            color={buttonColor}
            variant="tertiary"
            size="small"
            aria-label={t('Close')}
            icon={<Icon name="close" />}
          ></Button>
        )}
      </div>
    </div>
  );
};

// Same breakpoint as `useResponsive().isMobile` (ui-kit) and `breakpoint("mobile")` (SCSS)
const MOBILE_MEDIA_QUERY = "(max-width: 768px)";

export const addToast = (
  children: React.ReactNode,
  options: Parameters<typeof toast>[1] = {}
) => {
  const isMobile =
    typeof window !== "undefined" &&
    window.matchMedia(MOBILE_MEDIA_QUERY).matches;

  return toast(children, {
    position: isMobile ? "bottom-center" : "bottom-left",
    closeButton: false,
    className: "suite__toaster__wrapper",
    autoClose: 5000,
    transition: Slide,
    hideProgressBar: true,
    // On mobile the toast is bottom-centered and nearly full-width: the
    // horizontal dismiss threshold (80% of the toast width) is out of reach,
    // so dismiss with a downward swipe instead (threshold based on height).
    // The percent must differ from the default 80: react-toastify silently
    // multiplies 80 by 1.5 for the "y" direction (120% of the toast height).
    draggableDirection: isMobile ? "y" : "x",
    draggablePercent: isMobile ? 40 : 80,
    ...options,
  });
};
