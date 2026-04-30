import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/features/auth";
import { WidgetHelper } from "@/features/utils/widget-helper";

interface FeedbackWidgetProps {
  widget?: string;
}

export function FeedbackWidget({
  widget = "feedback",
}: FeedbackWidgetProps) {
  const { t } = useTranslation();
  const { user } = useAuth();

  const apiUrl = process.env.NEXT_PUBLIC_FEEDBACK_WIDGET_API_URL;
  const widgetPath = process.env.NEXT_PUBLIC_FEEDBACK_WIDGET_PATH;
  const channel = process.env.NEXT_PUBLIC_FEEDBACK_WIDGET_CHANNEL;

  const title: string = t("Do you have any feedback?");
  const placeholder: string = t("Share your feedback here...");
  const emailPlaceholder: string = t("Your email...");
  const submitText: string = t("Send Feedback");
  const successText: string = t("Thank you for your feedback!");
  const successText2: string = t("In case of questions, we'll get back to you soon.");
  const closeLabel: string = t("Close the feedback widget");

  useEffect(() => {
    if (!channel || !apiUrl || !widgetPath) return;
    if (typeof window === "undefined") return;

    WidgetHelper.pushCommand([
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
          successText2,
          closeLabel,
          // Add email parameter if user is logged in
          ...(user?.email && { email: user.email }),
        },
        script: `${widgetPath}feedback.js`,
        widget,
        label: title,
        closeLabel,
      },
    ]);

    WidgetHelper.loadScript(`${widgetPath}loader.js`);
  }, [title, channel, apiUrl, widgetPath, widget, placeholder, emailPlaceholder, submitText, successText, successText2, closeLabel, user?.email]);

  // This component doesn't render anything visible
  // The widget is injected via the script
  return null;
}
