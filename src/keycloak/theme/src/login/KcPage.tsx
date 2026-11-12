import { Suspense, lazy } from "react";
import DefaultPage from "keycloakify/login/DefaultPage";
import type { KcContext } from "./KcContext";
import { useI18n } from "./i18n";
import Template from "./Template";
import { classes } from "./classes";

const UserProfileFormFields = lazy(
  () => import("keycloakify/login/UserProfileFormFields"),
);

const Login = lazy(() => import("./pages/Login"));
const LoginOtp = lazy(() => import("./pages/LoginOtp"));
const LoginConfigTotp = lazy(() => import("./pages/LoginConfigTotp"));
const WebauthnRegister = lazy(() => import("./pages/WebauthnRegister"));
const WebauthnAuthenticate = lazy(() => import("./pages/WebauthnAuthenticate"));
const WebauthnError = lazy(() => import("./pages/WebauthnError"));
const SelectAuthenticator = lazy(() => import("./pages/SelectAuthenticator"));
const DeleteCredential = lazy(() => import("./pages/DeleteCredential"));
const LoginUpdatePassword = lazy(() => import("./pages/LoginUpdatePassword"));
const LoginUpdateProfile = lazy(() => import("./pages/LoginUpdateProfile"));
const LogoutConfirm = lazy(() => import("./pages/LogoutConfirm"));
const Info = lazy(() => import("./pages/Info"));
const Error = lazy(() => import("./pages/Error"));
const LoginPageExpired = lazy(() => import("./pages/LoginPageExpired"));

const doMakeUserConfirmPassword = true;

export default function KcPage(props: { kcContext: KcContext }) {
  const { kcContext } = props;
  const { i18n } = useI18n({ kcContext });

  return (
    <Suspense>
      {(() => {
        switch (kcContext.pageId) {
          case "login.ftl":
            return (
              <Login
                {...{ kcContext, i18n, classes }}
                Template={Template}
                doUseDefaultCss={false}
              />
            );
          case "login-otp.ftl":
            return (
              <LoginOtp
                {...{ kcContext, i18n, classes }}
                Template={Template}
                doUseDefaultCss={false}
              />
            );
          case "login-config-totp.ftl":
            return (
              <LoginConfigTotp
                {...{ kcContext, i18n, classes }}
                Template={Template}
                doUseDefaultCss={false}
              />
            );
          case "webauthn-register.ftl":
            return (
              <WebauthnRegister
                {...{ kcContext, i18n, classes }}
                Template={Template}
                doUseDefaultCss={false}
              />
            );
          case "webauthn-authenticate.ftl":
            return (
              <WebauthnAuthenticate
                {...{ kcContext, i18n, classes }}
                Template={Template}
                doUseDefaultCss={false}
              />
            );
          case "webauthn-error.ftl":
            return (
              <WebauthnError
                {...{ kcContext, i18n, classes }}
                Template={Template}
                doUseDefaultCss={false}
              />
            );
          case "select-authenticator.ftl":
            return (
              <SelectAuthenticator
                {...{ kcContext, i18n, classes }}
                Template={Template}
                doUseDefaultCss={false}
              />
            );
          case "delete-credential.ftl":
            return (
              <DeleteCredential
                {...{ kcContext, i18n, classes }}
                Template={Template}
                doUseDefaultCss={false}
              />
            );
          case "login-update-password.ftl":
            return (
              <LoginUpdatePassword
                {...{ kcContext, i18n, classes }}
                Template={Template}
                doUseDefaultCss={false}
              />
            );
          case "login-update-profile.ftl":
            return (
              <LoginUpdateProfile
                {...{ kcContext, i18n, classes }}
                Template={Template}
                doUseDefaultCss={false}
                UserProfileFormFields={UserProfileFormFields}
                doMakeUserConfirmPassword={doMakeUserConfirmPassword}
              />
            );
          case "logout-confirm.ftl":
            return (
              <LogoutConfirm
                {...{ kcContext, i18n, classes }}
                Template={Template}
                doUseDefaultCss={false}
              />
            );
          case "info.ftl":
            return (
              <Info
                {...{ kcContext, i18n, classes }}
                Template={Template}
                doUseDefaultCss={false}
              />
            );
          case "error.ftl":
            return (
              <Error
                {...{ kcContext, i18n, classes }}
                Template={Template}
                doUseDefaultCss={false}
              />
            );
          case "login-page-expired.ftl":
            return (
              <LoginPageExpired
                {...{ kcContext, i18n, classes }}
                Template={Template}
                doUseDefaultCss={false}
              />
            );
          default:
            return (
              <DefaultPage
                kcContext={kcContext}
                i18n={i18n}
                classes={classes}
                Template={Template}
                doUseDefaultCss={false}
                UserProfileFormFields={UserProfileFormFields}
                doMakeUserConfirmPassword={doMakeUserConfirmPassword}
              />
            );
        }
      })()}
    </Suspense>
  );
}
