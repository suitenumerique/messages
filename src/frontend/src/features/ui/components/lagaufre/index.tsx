import { useTranslation } from "react-i18next"
import { useEffect, useRef } from "react"
import { LaGaufreV2 } from "@gouvfr-lasuite/ui-kit";

const LAGAUFRE_SHADOW_HOST_ID = "lasuite-widget-lagaufre-shadow";

/**
 * A button that opens the lagaufre widget, backed by the ui-kit LaGaufreV2 component.
 */
export const LagaufreButton = () => {
  const { t } = useTranslation()
  const wrapperRef = useRef<HTMLDivElement>(null);
  const apiUrl = import.meta.env.NEXT_PUBLIC_LAGAUFRE_WIDGET_API_URL;
  const widgetPath = import.meta.env.NEXT_PUBLIC_LAGAUFRE_WIDGET_PATH;
  const isEnabled = apiUrl && widgetPath;

  // TODO: temporary workaround — remove once fixed upstream in the lagaufre
  // widget (its click-outside listener should use the capture phase).
  // The lagaufre widget only closes its popover from a bubble-phase document
  // click listener. Sibling popover triggers (language picker, feedback,
  // settings menu) stop click propagation to drive their own menus, so that
  // listener never runs and the gaufre stays open behind them. We close it from
  // a capture-phase listener, which fires before any stopPropagation can swallow
  // the click.
  useEffect(() => {
    if (!isEnabled) return;

    let isOpen = false;
    const onOpened = () => { isOpen = true; };
    const onClosed = () => { isOpen = false; };

    const onCaptureClick = (event: MouseEvent) => {
      if (!isOpen) return;
      const path = event.composedPath();
      const shadowHost = document.getElementById(LAGAUFRE_SHADOW_HOST_ID);
      // Ignore clicks on the gaufre button (let the widget toggle it) and inside
      // its popover, which is rendered in a shadow root appended to the body.
      const clickedGaufre =
        (shadowHost !== null && path.includes(shadowHost)) ||
        (wrapperRef.current !== null && path.includes(wrapperRef.current));
      if (clickedGaufre) return;
      document.dispatchEvent(new CustomEvent("lasuite-widget-lagaufre-close"));
    };

    document.addEventListener("lasuite-widget-lagaufre-opened", onOpened);
    document.addEventListener("lasuite-widget-lagaufre-closed", onClosed);
    document.addEventListener("click", onCaptureClick, true);

    return () => {
      document.removeEventListener("lasuite-widget-lagaufre-opened", onOpened);
      document.removeEventListener("lasuite-widget-lagaufre-closed", onClosed);
      document.removeEventListener("click", onCaptureClick, true);
    };
  }, [isEnabled]);

  if (!isEnabled) {
    return null;
  }

  return (
    <div ref={wrapperRef} style={{ display: "contents" }}>
      <LaGaufreV2
        widgetPath={widgetPath}
        apiUrl={apiUrl}
        label={t("Other services...")}
        closeLabel={t("Close the menu")}
      />
    </div>
  )
}
