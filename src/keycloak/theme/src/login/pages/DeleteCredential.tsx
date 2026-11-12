import { ButtonsGroup } from "@codegouvfr/react-dsfr/ButtonsGroup";
import type { PageProps } from "keycloakify/login/pages/PageProps";
import type { KcContext } from "../KcContext";
import type { I18n } from "../i18n";

export default function DeleteCredential(
  props: PageProps<
    Extract<KcContext, { pageId: "delete-credential.ftl" }>,
    I18n
  >,
) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;
  const { msgStr } = i18n;
  const { url, credentialLabel, messagesPerField } = kcContext;

  return (
    <Template
      kcContext={kcContext}
      i18n={i18n}
      doUseDefaultCss={doUseDefaultCss}
      classes={classes}
      displayMessage={!messagesPerField.existsError("username")}
      headerNode={msgStr("deleteCredentialTitle", credentialLabel)}
    >
      <p id="kc-delete-text">
        {msgStr("deleteCredentialMessage", credentialLabel)}
      </p>
      <form
        id="kc-delete-credential-form"
        action={url.loginAction}
        method="post"
      >
        <ButtonsGroup
          inlineLayoutWhen="always"
          buttonsEquisized
          buttons={[
            {
              children: msgStr("doConfirmDelete"),
              type: "submit",
              nativeButtonProps: {
                id: "kc-accept",
                name: "accept",
                value: "true",
              },
            },
            {
              children: msgStr("doCancel"),
              priority: "secondary",
              type: "submit",
              nativeButtonProps: {
                id: "kc-decline",
                name: "cancel-aia",
                value: "true",
              },
            },
          ]}
        />
      </form>
    </Template>
  );
}
