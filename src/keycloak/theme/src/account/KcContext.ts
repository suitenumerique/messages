import type { KcEnvName } from "../kc.gen";

export type KcContext = {
  themeType: "account";
  themeName: string;
  pageId: "index.ftl";
  serverBaseUrl: string;
  authUrl?: string;
  baseUrl:
    | string
    | {
        scheme: string;
        rawSchemeSpecificPart: string;
        authority: string;
        path: string;
      };
  realm: {
    name: string;
    registrationEmailAsUsername?: boolean;
    editUsernameAllowed?: boolean;
    isInternationalizationEnabled?: boolean;
  };
  clientId: string;
  resourceUrl: string;
  locale: string;
  supportedLocales?: Record<string, string>;
  msgJSON?: string;
  properties: Record<KcEnvName, string> & Record<string, string | undefined>;
  scope?: string;
  darkMode?: boolean;
  isSecureContext?: boolean;
  updateEmailFeatureEnabled?: boolean;
  updateEmailActionEnabled?: boolean;
};
