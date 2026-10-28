export type Lagaufre = {
  apiUrl: string;
  widgetPath: string;
};

export type Settings = {
  hideLogoutButton: boolean;
  twoFactorHelpUrl?: string;
  forgotPasswordUrl?: string;
  lagaufre?: Lagaufre;
};

const TRUE_VALUES = ["1", "true", "yes", "y"];

// Only a well-formed http(s) URL goes into an href, so a `javascript:` value
// cannot run and a broken value cannot replace the realm reset link.
function readUrl(value: string | undefined): string | undefined {
  const url = (value ?? "").trim();
  try {
    const parsed = new URL(url);
    const isHttp = parsed.protocol === "http:" || parsed.protocol === "https:";
    if (isHttp && parsed.hostname) return url;
    throw new Error("Invalid URL");
  } catch {
    return undefined;
  }
}

// Same true values as the django-configurations BooleanValue of the backend.
function readBoolean(value: string | undefined): boolean {
  return TRUE_VALUES.includes((value ?? "").trim().toLowerCase());
}

// Same JSON shape as FRONTEND_LAGAUFRE_WIDGET_CONFIG of the backend.
function readLagaufre(value: string | undefined): Lagaufre | undefined {
  let config: unknown;
  try {
    config = JSON.parse(value ?? "");
  } catch {
    return undefined;
  }
  if (typeof config !== "object" || config === null) return undefined;
  const { api_url, path } = config as Record<string, unknown>;
  const apiUrl = readUrl(typeof api_url === "string" ? api_url : undefined);
  const widgetPath = readUrl(typeof path === "string" ? path : undefined);
  return apiUrl && widgetPath ? { apiUrl, widgetPath } : undefined;
}

export function getSettings(
  properties: Record<string, string | undefined>,
): Settings {
  return {
    hideLogoutButton: readBoolean(properties.DSFR_THEME_HIDE_LOGOUT_BUTTON),
    twoFactorHelpUrl: readUrl(properties.DSFR_THEME_TWO_FACTOR_HELP_URL),
    forgotPasswordUrl: readUrl(properties.DSFR_THEME_FORGOT_PASSWORD_URL),
    lagaufre: readLagaufre(properties.DSFR_THEME_LAGAUFRE_WIDGET_CONFIG),
  };
}
