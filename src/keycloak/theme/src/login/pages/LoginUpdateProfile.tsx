import { useState } from "react";
import { Button } from "@codegouvfr/react-dsfr/Button";
import type { LazyOrNot } from "keycloakify/tools/LazyOrNot";
import { getKcClsx } from "keycloakify/login/lib/kcClsx";
import type { UserProfileFormFieldsProps } from "keycloakify/login/UserProfileFormFieldsProps";
import "../login.css";
import type { PageProps } from "keycloakify/login/pages/PageProps";
import type { KcContext } from "../KcContext";
import type { I18n } from "../i18n";

type LoginUpdateProfileProps = PageProps<
  Extract<KcContext, { pageId: "login-update-profile.ftl" }>,
  I18n
> & {
  UserProfileFormFields: LazyOrNot<
    (props: UserProfileFormFieldsProps) => React.ReactElement
  >;
  doMakeUserConfirmPassword: boolean;
};

export default function LoginUpdateProfile(props: LoginUpdateProfileProps) {
  const {
    kcContext,
    i18n,
    doUseDefaultCss,
    Template,
    classes,
    UserProfileFormFields,
    doMakeUserConfirmPassword,
  } = props;
  const { kcClsx } = getKcClsx({ doUseDefaultCss, classes });
  const { messagesPerField, url, isAppInitiatedAction } = kcContext;
  const { msg, msgStr } = i18n;
  const [isFormSubmittable, setIsFormSubmittable] = useState(false);

  return (
    <Template
      kcContext={kcContext}
      i18n={i18n}
      doUseDefaultCss={doUseDefaultCss}
      classes={classes}
      displayRequiredFields
      headerNode={msg("loginProfileTitle")}
      displayMessage={messagesPerField.exists("global")}
    >
      <form id="kc-update-profile-form" action={url.loginAction} method="post">
        <UserProfileFormFields
          kcContext={kcContext}
          i18n={i18n}
          kcClsx={kcClsx}
          onIsFormSubmittableValueChange={setIsFormSubmittable}
          doMakeUserConfirmPassword={doMakeUserConfirmPassword}
        />
        <Button
          type="submit"
          disabled={!isFormSubmittable}
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
