import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ConfigRetrieve200 } from "@/features/api/gen";

const API_CONFIG = {
  ENVIRONMENT: "production",
  RELEASE: "1.2.3",
  LANGUAGES: [
    ["en-us", "English"],
    ["fr-fr", "Français"],
  ],
  LANGUAGE_CODE: "fr-fr",
  AI_ENABLED: true,
  FEATURE_AI_SUMMARY: false,
  FEATURE_AI_AUTOLABELS: false,
  FEATURE_MAILBOX_ADMIN_CHANNELS: [],
  SCHEMA_CUSTOM_ATTRIBUTES_USER: {},
  SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN: {},
  MAX_OUTGOING_ATTACHMENT_SIZE: 100,
  MAX_OUTGOING_BODY_SIZE: 100,
  MAX_RECIPIENTS_PER_MESSAGE: 10,
  MAX_TEMPLATE_IMAGE_SIZE: 100,
  IMAGE_PROXY_ENABLED: false,
  FEATURE_MAILDOMAIN_CREATE: true,
  FEATURE_MAILDOMAIN_MANAGE_ACCESSES: true,
  FEATURE_MAILDOMAIN_MANAGE_TOTP: false,
  FEATURE_THREAD_SPLIT: true,
  MESSAGES_MANUAL_RETRY_MAX_AGE: 0,
  FRONTEND_SILENT_LOGIN_ENABLED: false,
  SENTRY_DSN: "https://public@sentry.example.com/1",
  FRONTEND_THEME_CONFIG: { theme: "dsfr" },
  FRONTEND_FORCED_DEFAULT_LANGUAGE: true,
  FRONTEND_MULTIPART_UPLOAD_CHUNK_SIZE_MB: 42,
  FRONTEND_HELP_CENTER_URL: "https://help.example.com",
  FRONTEND_FEEDBACK_WIDGET_CONFIG: {
    api_url: "https://feedback.example.com",
    path: "https://feedback.example.com/static/",
    channel: "support",
    home_channel: "home",
  },
  FRONTEND_LAGAUFRE_WIDGET_CONFIG: {
    api_url: "https://lagaufre.example.com",
    path: "https://lagaufre.example.com/static/",
  },
} as unknown as ConfigRetrieve200;

// The module keeps a warn-once registry, so each test imports a fresh copy.
const importResolve = async () => await import("./resolve");

describe("resolveConfig", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("uses API values first and normalizes languages to BCP 47", async () => {
    vi.stubEnv("NEXT_PUBLIC_HELP_CENTER_URL", "https://deprecated.example.com");
    const { resolveConfig } = await importResolve();

    const config = resolveConfig(API_CONFIG);

    expect(config.LANGUAGES).toEqual([
      ["en-US", "English"],
      ["fr-FR", "Français"],
    ]);
    expect(config.BASE_LANGUAGE).toBe("fr-FR");
    expect(config.IS_LANGUAGE_FORCED).toBe(true);
    expect(config.THEME_CONFIG).toEqual({ theme: "dsfr" });
    expect(config.SENTRY_DSN).toBe("https://public@sentry.example.com/1");
    expect(config.SENTRY_ENVIRONMENT).toBe("production");
    expect(config.RELEASE).toBe("1.2.3");
    expect(config.MULTIPART_UPLOAD_CHUNK_SIZE_MB).toBe(42);
    // API wins over the deprecated env var
    expect(config.HELP_CENTER_URL).toBe("https://help.example.com");
    expect(config.FEEDBACK_WIDGET.channel).toBe("support");
    expect(config.LAGAUFRE_WIDGET.api_url).toBe("https://lagaufre.example.com");
  });

  it("falls back on deprecated env vars when the API is unreachable", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.stubEnv("NEXT_PUBLIC_THEME_CONFIG", '{"theme": "anct"}');
    vi.stubEnv("NEXT_PUBLIC_LANGUAGES", '[["de-DE","Deutsch"]]');
    vi.stubEnv("NEXT_PUBLIC_DEFAULT_LANGUAGE", "de-DE");
    vi.stubEnv("NEXT_PUBLIC_FORCED_DEFAULT_LANGUAGE", "true");
    vi.stubEnv("NEXT_PUBLIC_SENTRY_DSN", "https://public@sentry.example.com/2");
    vi.stubEnv("NEXT_PUBLIC_SENTRY_ENVIRONMENT", "staging");
    vi.stubEnv("NEXT_PUBLIC_MULTIPART_UPLOAD_CHUNK_SIZE", "50");
    vi.stubEnv("NEXT_PUBLIC_HELP_CENTER_URL", "https://help.example.com");
    vi.stubEnv("NEXT_PUBLIC_FEEDBACK_WIDGET_CHANNEL", "support");
    const { resolveConfig } = await importResolve();

    const config = resolveConfig(undefined);

    expect(config.THEME_CONFIG).toEqual({ theme: "anct" });
    expect(config.LANGUAGES).toEqual([["de-DE", "Deutsch"]]);
    expect(config.BASE_LANGUAGE).toBe("de-DE");
    expect(config.IS_LANGUAGE_FORCED).toBe(true);
    expect(config.SENTRY_DSN).toBe("https://public@sentry.example.com/2");
    expect(config.SENTRY_ENVIRONMENT).toBe("staging");
    expect(config.MULTIPART_UPLOAD_CHUNK_SIZE_MB).toBe(50);
    expect(config.HELP_CENTER_URL).toBe("https://help.example.com");
    expect(config.FEEDBACK_WIDGET.channel).toBe("support");
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining("NEXT_PUBLIC_THEME_CONFIG is deprecated"),
    );
  });

  it("falls back on deprecated env vars for keys the API omits", async () => {
    // The backend omits FRONTEND_* settings left unconfigured so that it
    // never overrides the deprecated env vars with its own defaults.
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.stubEnv("NEXT_PUBLIC_THEME_CONFIG", '{"theme": "anct"}');
    vi.stubEnv("NEXT_PUBLIC_FORCED_DEFAULT_LANGUAGE", "true");
    vi.stubEnv("NEXT_PUBLIC_MULTIPART_UPLOAD_CHUNK_SIZE", "50");
    vi.stubEnv("NEXT_PUBLIC_HELP_CENTER_URL", "https://help.example.com");
    vi.stubEnv("NEXT_PUBLIC_FEEDBACK_WIDGET_CHANNEL", "support");
    const { resolveConfig } = await importResolve();

    const omittedKeys = [
      "FRONTEND_THEME_CONFIG",
      "FRONTEND_FORCED_DEFAULT_LANGUAGE",
      "FRONTEND_MULTIPART_UPLOAD_CHUNK_SIZE_MB",
      "FRONTEND_HELP_CENTER_URL",
      "FRONTEND_FEEDBACK_WIDGET_CONFIG",
    ];
    const partialApiConfig = Object.fromEntries(
      Object.entries(API_CONFIG).filter(([key]) => !omittedKeys.includes(key)),
    ) as ConfigRetrieve200;
    const config = resolveConfig(partialApiConfig);

    expect(config.THEME_CONFIG).toEqual({ theme: "anct" });
    expect(config.IS_LANGUAGE_FORCED).toBe(true);
    expect(config.MULTIPART_UPLOAD_CHUNK_SIZE_MB).toBe(50);
    expect(config.HELP_CENTER_URL).toBe("https://help.example.com");
    expect(config.FEEDBACK_WIDGET.channel).toBe("support");
  });

  it("warns only once per deprecated env var", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.stubEnv("NEXT_PUBLIC_HELP_CENTER_URL", "https://help.example.com");
    const { resolveConfig } = await importResolve();

    resolveConfig(undefined);
    resolveConfig(undefined);

    const helpCenterWarnings = warn.mock.calls.filter(([message]) =>
      String(message).includes("NEXT_PUBLIC_HELP_CENTER_URL"),
    );
    expect(helpCenterWarnings).toHaveLength(1);
  });

  it("uses hardcoded defaults when neither API nor env vars are set", async () => {
    const { resolveConfig, DEFAULT_LANGUAGES } = await importResolve();

    const config = resolveConfig(undefined);

    expect(config.THEME_CONFIG).toEqual({ theme: "white-label" });
    expect(config.LANGUAGES).toEqual(DEFAULT_LANGUAGES);
    expect(config.BASE_LANGUAGE).toBe("en-US");
    expect(config.IS_LANGUAGE_FORCED).toBe(false);
    expect(config.SENTRY_DSN).toBeUndefined();
    expect(config.MULTIPART_UPLOAD_CHUNK_SIZE_MB).toBe(100);
    expect(config.HELP_CENTER_URL).toBeUndefined();
    expect(config.FEEDBACK_WIDGET).toEqual({});
    expect(config.RELEASE).toBe("NA");
  });

  it("ignores invalid JSON in deprecated env vars", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.stubEnv("NEXT_PUBLIC_THEME_CONFIG", "{invalid json");
    const { resolveConfig } = await importResolve();

    const config = resolveConfig(undefined);

    expect(config.THEME_CONFIG).toEqual({ theme: "white-label" });
  });

  it("treats empty deprecated env vars as unset", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.stubEnv("NEXT_PUBLIC_HELP_CENTER_URL", "");
    const { resolveConfig } = await importResolve();

    const config = resolveConfig(undefined);

    expect(config.HELP_CENTER_URL).toBeUndefined();
    expect(warn).not.toHaveBeenCalled();
  });
});

describe("toBCP47", () => {
  it("normalizes region casing and keeps region-less codes", async () => {
    const { toBCP47 } = await importResolve();
    expect(toBCP47("en-us")).toBe("en-US");
    expect(toBCP47("fr-FR")).toBe("fr-FR");
    expect(toBCP47("fr")).toBe("fr");
  });
});
