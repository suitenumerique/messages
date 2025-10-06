import { useTranslation } from "react-i18next"
import { useState, useEffect } from "react"
import { Button } from "@openfun/cunningham-react";

/**
 * A button that opens the lagaufre widget
 */
export const LagaufreButton = () => {
  const { t } = useTranslation()
  const [isWidgetInitialized, setIsWidgetInitialized] = useState(false)
  
  const apiUrl = process.env.NEXT_PUBLIC_LAGAUFRE_WIDGET_API_URL; 
  const widgetPath = process.env.NEXT_PUBLIC_LAGAUFRE_WIDGET_PATH;

  const label: string = t("lagaufre_widget.label");
  const closeLabel: string = t("lagaufre_widget.close_label");

  // Initialize widget on component mount
  useEffect(() => {
    if (typeof window == "undefined" || !widgetPath || !apiUrl) return;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any)._stmsg_widget = (window as any)._stmsg_widget || [];
    
    // Construct script URLs from the base path
    const feedbackScript = `${widgetPath}lagaufre.js`;

    document.addEventListener("stmsg-widget-lagaufre-closed", () => {
        // Focus the button
        (document.querySelector(".lagaufre-button") as HTMLElement)?.focus();
    });

    // Load the loader script if not already loaded
    if (!document.querySelector(`script[src="${feedbackScript}"]`)) {
        const script = document.createElement("script");
        script.async = true;
        script.src = feedbackScript;
        const firstScript = document.getElementsByTagName("script")[0];
        if (firstScript && firstScript.parentNode) {
            firstScript.parentNode.insertBefore(script, firstScript);
        }
    }

    // Initialize the widget
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any)._stmsg_widget.push([
    "lagaufre",
    "init",
    {
        api: apiUrl,
        label,
        closeLabel,
        position: 'fixed',
        top: 53,
        right: 12
    },
    ]);

    setIsWidgetInitialized(true);
  }, [apiUrl, widgetPath, label, closeLabel]);

  const toggleWidget = () => {
    if (!isWidgetInitialized) return;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any)._stmsg_widget.push([
      "lagaufre",
      "toggle"
    ]);
  }


  return (
    <Button
          onClick={toggleWidget}
          icon={<svg width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg">
            <defs>
              <path id="square" d="M2.7959 0.5C3.26483 0.5 3.49956 0.49985 3.68848 0.564453C4.03934 0.684581 4.31542 0.960658 4.43555 1.31152C4.50015 1.50044 4.5 1.73517 4.5 2.2041V2.7959C4.5 3.26483 4.50015 3.49956 4.43555 3.68848C4.31542 4.03934 4.03934 4.31542 3.68848 4.43555C3.49956 4.50015 3.26483 4.5 2.7959 4.5H2.2041C1.73517 4.5 1.50044 4.50015 1.31152 4.43555C0.960658 4.31542 0.684581 4.03934 0.564453 3.68848C0.49985 3.49956 0.5 3.26483 0.5 2.7959V2.2041C0.5 1.73517 0.49985 1.50044 0.564453 1.31152C0.684581 0.960658 0.960658 0.684581 1.31152 0.564453C1.50044 0.49985 1.73517 0.5 2.2041 0.5H2.7959Z" />
            </defs>
            <use href="#square" transform="translate(0, 0)"/>
            <use href="#square" transform="translate(6.5, 0)"/>
            <use href="#square" transform="translate(13, 0)"/>
            <use href="#square" transform="translate(0, 6.5)"/>
            <use href="#square" transform="translate(6.5, 6.5)"/>
            <use href="#square" transform="translate(13, 6.5)"/>
            <use href="#square" transform="translate(0, 13)"/>
            <use href="#square" transform="translate(6.5, 13)"/>
            <use href="#square" transform="translate(13, 13)"/>
          </svg>}
          aria-label={t("lagaufre_widget.label")}
          color="tertiary-text"
          className="lagaufre-button"
     />
   
  )
}
