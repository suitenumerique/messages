import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/features/auth";

interface FeedbackWidgetProps {
  apiUrl?: string;
  widgetPath?: string;
  widget?: string;
  channel?: string;
}

export function FeedbackWidget({
  apiUrl = process.env.NEXT_PUBLIC_FEEDBACK_WIDGET_API_URL,
  widgetPath = process.env.NEXT_PUBLIC_FEEDBACK_WIDGET_PATH,
  channel = process.env.NEXT_PUBLIC_FEEDBACK_WIDGET_CHANNEL,
  widget = "feedback",
}: FeedbackWidgetProps) {
  const { t } = useTranslation();
  const { user } = useAuth();

  const title: string = t("feedback_widget.title");
  const placeholder: string = t("feedback_widget.placeholder");
  const emailPlaceholder: string = t("feedback_widget.email_placeholder");
  const submitText: string = t("feedback_widget.submit_text");
  const successText: string = t("feedback_widget.success_text");

  useEffect(() => {
    // Initialize the widget array if it doesn't exist
    if (typeof window !== "undefined" && widgetPath) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (window as any)._stmsg_widget = (window as any)._stmsg_widget || [];
      
      // Construct script URLs from the base path
      const loaderScript = `${widgetPath}loader.js`;
      const feedbackScript = `${widgetPath}feedback.js`;
      
      // Push the widget configuration
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (window as any)._stmsg_widget.push([
        "loader",
        "init",
        {
          params: {
            title,
            api: apiUrl,
            channel,
            placeholder,
            emailPlaceholder,
            submitText,
            successText,
            // Add email parameter if user is logged in
            ...(user?.email && { email: user.email }),
          },
          script: feedbackScript,
          widget,
          label: title,
        },
      ]);

      // Load the loader script if not already loaded
      if (!document.querySelector(`script[src="${loaderScript}"]`)) {
        const script = document.createElement("script");
        script.async = true;
        script.src = loaderScript;
        const firstScript = document.getElementsByTagName("script")[0];
        if (firstScript && firstScript.parentNode) {
          firstScript.parentNode.insertBefore(script, firstScript);
        }
      }
    }
  }, [title, apiUrl, widgetPath, widget, emailPlaceholder, submitText, successText, user?.email]);

  // This component doesn't render anything visible
  // The widget is injected via the script
  return null;
}
