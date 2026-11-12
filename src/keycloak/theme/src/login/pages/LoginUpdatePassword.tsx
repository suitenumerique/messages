import { Button } from "@codegouvfr/react-dsfr/Button";
import { PasswordInput } from "@codegouvfr/react-dsfr/blocks/PasswordInput";
import "../login.css";
import { LogoutOtherSessions } from "../components/LogoutOtherSessions";
import type { PageProps } from "keycloakify/login/pages/PageProps";
import type { KcContext } from "../KcContext";
import type { I18n } from "../i18n";

export default function LoginUpdatePassword(
  props: PageProps<
    Extract<KcContext, { pageId: "login-update-password.ftl" }>,
    I18n
  >,
) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;
  const { msg, msgStr } = i18n;
  const { url, messagesPerField, isAppInitiatedAction } = kcContext;

  return (
    <Template
      kcContext={kcContext}
      i18n={i18n}
      doUseDefaultCss={doUseDefaultCss}
      classes={classes}
      displayMessage={
        !messagesPerField.existsError("password", "password-confirm")
      }
      headerNode={msg("updatePasswordTitle")}
    >
      <form id="kc-passwd-update-form" action={url.loginAction} method="post">
        <PasswordInput
          label={msgStr("passwordNew")}
          messagesHint=""
          messages={
            messagesPerField.existsError("password")
              ? [
                  {
                    severity: "error",
                    message: messagesPerField.get("password"),
                  },
                ]
              : []
          }
          nativeInputProps={{
            id: "password-new",
            name: "password-new",
            autoComplete: "new-password",
            autoFocus: true,
            required: true,
          }}
        />
        <PasswordInput
          label={msgStr("passwordConfirm")}
          messagesHint=""
          messages={
            messagesPerField.existsError("password-confirm")
              ? [
                  {
                    severity: "error",
                    message: messagesPerField.get("password-confirm"),
                  },
                ]
              : []
          }
          nativeInputProps={{
            id: "password-confirm",
            name: "password-confirm",
            autoComplete: "new-password",
            required: true,
          }}
        />
        <LogoutOtherSessions i18n={i18n} />
        <Button
          type="submit"
          className="lst-btn-full fr-mb-1w"
          nativeButtonProps={{ name: "login" }}
        >
          {msgStr("doSubmit")}
        </Button>
        {isAppInitiatedAction && (
          <Button
            type="submit"
            priority="secondary"
            className="lst-btn-full"
            nativeButtonProps={{
              name: "cancel-aia",
              value: "true",
              formNoValidate: true,
            }}
          >
            {msgStr("doCancel")}
          </Button>
        )}
      </form>
    </Template>
  );
}
