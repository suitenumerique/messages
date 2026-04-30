import { useTranslation } from "react-i18next"
import { useState, useEffect, useRef } from "react"
import { Button, ButtonElement } from "@gouvfr-lasuite/cunningham-react";
import { WidgetHelper } from "@/features/utils/widget-helper";

/**
 * A button that opens the lagaufre widget
 */
export const LagaufreButton = () => {
  const { t } = useTranslation()
  const [isWidgetInitialized, setIsWidgetInitialized] = useState(false)
  const buttonRef = useRef<ButtonElement>(null);
  const apiUrl = process.env.NEXT_PUBLIC_LAGAUFRE_WIDGET_API_URL;
  const widgetPath = process.env.NEXT_PUBLIC_LAGAUFRE_WIDGET_PATH;

  const label: string = t("Other services...");
  const closeLabel: string = t("Close the menu");

  // Initialize widget on component mount
  useEffect(() => {
    if (typeof window == "undefined" || !widgetPath || !apiUrl) return;

    document.addEventListener("stmsg-widget-lagaufre-closed", () => {
        // Focus the button
        buttonRef.current?.focus();
        buttonRef.current?.setAttribute("aria-expanded", "false");
    });

    document.addEventListener("stmsg-widget-lagaufre-opened", () => {
        buttonRef.current?.setAttribute("aria-expanded", "true");
    });

    WidgetHelper.loadScript(`${widgetPath}lagaufre.js`);
    WidgetHelper.pushCommand([
      "lagaufre",
      "init",
      {
        api: apiUrl,
        label,
        closeLabel,
        position: "fixed",
        top: 53,
        right: 12,
      },
    ]);

    setIsWidgetInitialized(true);
  }, [apiUrl, widgetPath, label, closeLabel]);

  const toggleWidget = () => {
    if (!isWidgetInitialized) return;
    WidgetHelper.pushCommand(["lagaufre", "toggle"]);
  }

  if (!widgetPath || !apiUrl) {
    return null;
  }

  // Must include the explicit fill color here because Firefox doesn't seem to apply the CSS style (!?)
  return (
    <Button
          onClick={toggleWidget}
          ref={buttonRef}
          icon={<LaGauffreIcon />}
          aria-label={label}
          aria-expanded="false"
          variant="tertiary"
          className="lagaufre-button"
     />

  )
}

const LaGauffreIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" fill="none"><defs><path id="a" fill="currentColor" d="M2.796.5c.469 0 .704 0 .892.064.351.12.627.397.748.748.064.188.064.423.064.892v.592c0 .469 0 .704-.064.892-.12.351-.397.627-.748.748-.188.064-.423.064-.892.064h-.592c-.469 0-.704 0-.892-.064a1.201 1.201 0 0 1-.748-.748C.5 3.5.5 3.265.5 2.796v-.592c0-.469 0-.704.064-.892.12-.351.397-.627.748-.748C1.5.5 1.735.5 2.204.5h.592Z"/></defs><use href="#a"/><use href="#a" transform="translate(6.5)"/><use href="#a" transform="translate(13)"/><use href="#a" transform="translate(0 6.5)"/><use href="#a" transform="translate(6.5 6.5)"/><use href="#a" transform="translate(13 6.5)"/><use href="#a" transform="translate(0 13)"/><use href="#a" transform="translate(6.5 13)"/><use href="#a" transform="translate(13 13)"/></svg>
)
