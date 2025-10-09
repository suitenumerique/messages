import { Icon, IconType } from "@gouvfr-lasuite/ui-kit"
import { Button, ButtonProps } from "@openfun/cunningham-react"
import { useTranslation } from "react-i18next"
import { useAuth } from "@/features/auth";

/**
 * A button that opens the feedback widget
 */
export const SurveyButton = (props: ButtonProps) => {
  const { t } = useTranslation()
  const { user } = useAuth();
  
  const apiUrl = process.env.NEXT_PUBLIC_FEEDBACK_WIDGET_API_URL; 
  const widgetPath = process.env.NEXT_PUBLIC_FEEDBACK_WIDGET_PATH;
  const channel = process.env.NEXT_PUBLIC_FEEDBACK_WIDGET_CHANNEL;
  
  if (!channel || !apiUrl || !widgetPath) return null;

  const title: string = t("Do you have any feedback?");
  const placeholder: string = t("Share your feedback here...");
  const emailPlaceholder: string = t("Your email...");
  const submitText: string = t("Send Feedback");
  const successText: string = t("Thank you for your feedback!");
  const successText2: string = t("In case of questions, we'll get back to you soon.");
  const closeLabel: string = t("Close the feedback widget");

  const showWidget = () => {
    // Initialize the widget array if it doesn't exist
    if (typeof window !== "undefined" && widgetPath) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (window as any)._stmsg_widget = (window as any)._stmsg_widget || [];
      
      // Construct script URLs from the base path
      const feedbackScript = `${widgetPath}feedback.js`;
      
      // Push the widget configuration
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (window as any)._stmsg_widget.push([
        "feedback",
        "init",
        {
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
      ]);

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
    }
  }


  return (
    <Button
      {...props}
      icon={<Icon name="info" type={IconType.FILLED} />}
      color="tertiary"
      className="feedback-button"
      title={t("Do you have any feedback?")}
      onClick={showWidget}
    >
      {t("Feedback?")}
    </Button>
  )
}
