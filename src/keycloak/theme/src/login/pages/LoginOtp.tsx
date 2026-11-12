import { useState } from "react";
import { Input } from "@codegouvfr/react-dsfr/Input";
import { RadioButtons } from "@codegouvfr/react-dsfr/RadioButtons";
import { Button } from "@codegouvfr/react-dsfr/Button";
import { kcSanitize } from "keycloakify/lib/kcSanitize";
import type { PageProps } from "keycloakify/login/pages/PageProps";
import type { KcContext } from "../KcContext";
import type { I18n } from "../i18n";
import "../login.css";

export default function LoginOtp(
  props: PageProps<Extract<KcContext, { pageId: "login-otp.ftl" }>, I18n>,
) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;
  const { otpLogin, url, messagesPerField } = kcContext;
  const { msg } = i18n;

  const [isSubmitting, setIsSubmitting] = useState(false);
  const hasError = messagesPerField.existsError("totp");

  return (
    <Template
      kcContext={kcContext}
      i18n={i18n}
      doUseDefaultCss={doUseDefaultCss}
      classes={classes}
      displayMessage={!hasError}
      headerNode={msg("doLogIn")}
    >
      <form
        id="kc-otp-login-form"
        action={url.loginAction}
        onSubmit={() => {
          setIsSubmitting(true);
          return true;
        }}
        method="post"
      >
        {otpLogin.userOtpCredentials.length > 1 && (
          <RadioButtons
            name="selectedCredentialId"
            options={otpLogin.userOtpCredentials.map(
              (otpCredential, index) => ({
                label: otpCredential.userLabel,
                nativeInputProps: {
                  id: `kc-otp-credential-${index}`,
                  value: otpCredential.id,
                  defaultChecked:
                    otpCredential.id === otpLogin.selectedCredentialId,
                },
              }),
            )}
          />
        )}
        <Input
          label={msg("loginOtpOneTime")}
          state={hasError ? "error" : "default"}
          stateRelatedMessage={
            hasError ? (
              <span
                id="input-error-otp-code"
                aria-live="polite"
                dangerouslySetInnerHTML={{
                  __html: kcSanitize(messagesPerField.get("totp")),
                }}
              />
            ) : undefined
          }
          nativeInputProps={{
            id: "otp",
            name: "otp",
            autoComplete: "one-time-code",
            inputMode: "numeric",
            autoFocus: true,
          }}
        />
        <Button
          className="lst-btn-full"
          disabled={isSubmitting}
          nativeButtonProps={{ id: "kc-login", name: "login", type: "submit" }}
        >
          {msg("doLogIn")}
        </Button>
      </form>
    </Template>
  );
}
