import { useTranslation } from "react-i18next"
import { useEffect, useRef } from "react"
import { LaGaufreV2 } from "@gouvfr-lasuite/ui-components";
import { useConfig } from "@/features/providers/config";

const LAGAUFRE_SHADOW_HOST_ID = "lasuite-widget-lagaufre-shadow";
/**
 * Dispatched on document by the widget script when it processes an init
 * command, i.e. each time LaGaufreV2 mounts and pushes its config to
 * `window._lasuite_widget` (or, for inits pushed before the script loaded,
 * once it has loaded). The event detail is the widget config.
 */
const LAGAUFRE_INIT_EVENT = "lasuite-widget-lagaufre-init";
/**
 * Dispatched on document by the widget script at the very end of its init
 * handler: services fetched (or fetch failed), listeners bound and the
 * instance flagged as live, so the next init will properly destroy it.
 * Like every widget event, it is a non-bubbling CustomEvent.
 */
const LAGAUFRE_INITIALIZED_EVENT = "lasuite-widget-lagaufre-initialized";

// TODO: temporary workaround — remove once fixed upstream in the lagaufre
// widget (an init should cancel or destroy any in-flight init).
// LaGaufreV2 pushes an init each time it mounts. The widget only destroys the
// previous instance once that one has finished fetching its services, so a
// remount during that fetch (e.g. the header remounted by a redirect from "/"
// or "/domain") ends up with two live instances and one click opens two
// gaufres. We hold back any init received while another is in flight and
// replay only the latest one, as earlier ones target an unmounted button.
// Widget events are dispatched on document without bubbling, so window only
// sees them in the capture phase, which also runs before the widget's own
// listener on document.
let isWidgetInitializing = false;
let pendingInit: { detail: unknown } | null = null;

if (typeof window !== "undefined") {
  window.addEventListener(LAGAUFRE_INIT_EVENT, (event) => {
    if (!isWidgetInitializing) {
      isWidgetInitializing = true;
      return;
    }
    event.stopImmediatePropagation();
    pendingInit = { detail: (event as CustomEvent).detail };
  }, true);

  window.addEventListener(LAGAUFRE_INITIALIZED_EVENT, () => {
    isWidgetInitializing = false;
    if (!pendingInit) return;
    const { detail } = pendingInit;
    pendingInit = null;
    queueMicrotask(() => {
      document.dispatchEvent(new CustomEvent(LAGAUFRE_INIT_EVENT, { detail }));
    });
  }, true);
}

/**
 * A button that opens the lagaufre widget, backed by the ui-kit LaGaufreV2 component.
 */
export const LagaufreButton = () => {
  const { t } = useTranslation()
  const { LAGAUFRE_WIDGET } = useConfig();
  const wrapperRef = useRef<HTMLDivElement>(null);
  const { api_url: apiUrl, path: widgetPath } = LAGAUFRE_WIDGET;
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
