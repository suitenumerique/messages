import { useConfig } from "@/features/providers/config"
import { Icon, IconType } from "@gouvfr-lasuite/ui-kit"
import { Button, ButtonProps } from "@openfun/cunningham-react"
import { usePostHog } from "posthog-js/react"
import { useTranslation } from "react-i18next"

/**
 * A button that opens the PostHog survey modal.
 *
 * This button is only visible if PostHog is loaded. To work, a survey must be
 * created in PostHog with type Feedback button and as CSS Selector you must
 * use `#posthog-feedback-survey`.
 *
 */
export const PostHogSurveyButton = (props: ButtonProps) => {
  const { t } = useTranslation()
  const posthog = usePostHog()
  const config = useConfig()

  if (!config.POSTHOG_SURVEY_ID || !posthog.__loaded) return null;

  return (
    <Button
      {...props}
      icon={<Icon name="info" type={IconType.FILLED} />}
      color="tertiary"
      className="feedback-button posthog-feedback-survey"
      title={t("posthog.cta")}
    >
      {t("posthog.cta")}
    </Button>
  )
}
