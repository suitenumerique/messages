import { Button } from "@codegouvfr/react-dsfr/Button";
import type { PageProps } from "keycloakify/login/pages/PageProps";
import type { KcContext } from "../KcContext";
import type { I18n } from "../i18n";
import "../login.css";

export default function WebauthnError(
  props: PageProps<Extract<KcContext, { pageId: "webauthn-error.ftl" }>, I18n>,
) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;
  const { url, isAppInitiatedAction, execution } = kcContext;
  const { msg } = i18n;

  const retry = () => {
    const form = document.getElementById(
      "kc-error-credential-form",
    ) as HTMLFormElement;
    (document.getElementById("isSetRetry") as HTMLInputElement).value = "retry";
    (document.getElementById("executionValue") as HTMLInputElement).value =
      execution;
    form.requestSubmit();
  };

  return (
    <Template
      kcContext={kcContext}
      i18n={i18n}
      doUseDefaultCss={doUseDefaultCss}
      classes={classes}
      displayMessage
      headerNode={msg("webauthn-error-title")}
    >
      <form
        id="kc-error-credential-form"
        action={url.loginAction}
        method="post"
      >
        <input
          type="hidden"
          id="executionValue"
          name="authenticationExecution"
        />
        <input type="hidden" id="isSetRetry" name="isSetRetry" />
      </form>
      <Button
        className="lst-btn-full fr-mb-2w"
        onClick={retry}
        nativeButtonProps={{ id: "kc-try-again", name: "try-again" }}
      >
        {msg("doTryAgain")}
      </Button>
      {isAppInitiatedAction && (
        <form
          action={url.loginAction}
          id="kc-webauthn-settings-form"
          method="post"
        >
          <Button
            priority="secondary"
            className="lst-btn-full"
            nativeButtonProps={{
              id: "cancelWebAuthnAIA",
              name: "cancel-aia",
              value: "true",
              type: "submit",
            }}
          >
            {msg("doCancel")}
          </Button>
        </form>
      )}
    </Template>
  );
}
