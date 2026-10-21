export type Brand = {
  homeUrl: string;
  serviceTitle: string;
  brandTopHtml: string;
};

export function getBrand(
  properties: Record<string, string | undefined>,
  fallbackHomeUrl: string,
): Brand {
  const configuredHomeUrl = properties.DSFR_THEME_HOME_URL;
  return {
    homeUrl:
      configuredHomeUrl && configuredHomeUrl !== "/"
        ? configuredHomeUrl
        : fallbackHomeUrl,
    serviceTitle: properties.DSFR_THEME_SERVICE_TITLE || "Messages",
    brandTopHtml: properties.DSFR_THEME_BRAND_TOP || "République<br/>Française",
  };
}

export function getAccountConsoleUrl(realmName: string): string {
  const segment = `/realms/${encodeURIComponent(realmName)}/`;
  const index = window.location.pathname.indexOf(segment);
  const prefix = index === -1 ? "" : window.location.pathname.slice(0, index);
  return new URL(`${prefix}${segment}account/`, window.location.origin).href;
}
