import { useEffect, useState } from "react";
import { App } from "@capacitor/app";
import { Capacitor } from "@capacitor/core";

export type AppVersion = {
    /**
     * Version of the running web app (package.json). On a native platform this
     * is the version of the *bundle*, which OTA can move ahead of the installed
     * app — hence a value of its own rather than a single "app version".
     */
    web: string;
    /** Commit stamp of the running bundle. Support-facing, never displayed as-is. */
    source: string;
    /**
     * Version of the installed native app, as published on the store
     * (`appVersion` in capacitor.config.ts). Undefined on the web, and until
     * the native call resolves.
     */
    native?: string;
    /** Native build number (Android versionCode / iOS CFBundleVersion). */
    nativeBuild?: string;
};

/**
 * Versions of the running app, for display and for support reports.
 *
 * The web version is baked in at build time; the native one is read from the
 * OS at runtime, so it reflects what the user actually installed rather than
 * what the bundle was built alongside.
 */
export const useAppVersion = (): AppVersion => {
    const [native, setNative] = useState<Pick<AppVersion, "native" | "nativeBuild">>({});

    useEffect(() => {
        // `Capacitor.isNativePlatform()` rather than `isNativePlatform()`: the
        // latter's DEV_FAKE_NATIVE escape hatch turns on native *UI* paths in a
        // desktop browser, where the plugin is not implemented and would throw.
        if (!Capacitor.isNativePlatform()) return;

        let cancelled = false;
        App.getInfo()
            .then(({ version, build }) => {
                if (!cancelled) setNative({ native: version, nativeBuild: build });
            })
            // A version we cannot read is not worth an error path: the entry
            // still shows the web version, and the native line simply stays out.
            .catch(() => undefined);

        return () => {
            cancelled = true;
        };
    }, []);

    return { web: __WEB_APP_VERSION__, source: __SOURCE_VERSION__, ...native };
};

/**
 * One-line, copy-ready version report for support: every number that
 * identifies the running app, including the commit the bundle was built from.
 */
export const formatVersionReport = (version: AppVersion): string => {
    const platform = Capacitor.getPlatform();

    return [
        version.native && `app ${version.native} (${version.nativeBuild ?? "?"})`,
        `web ${version.web} (${version.source})`,
        // Only worth stating where it disambiguates: a web report is already
        // self-evidently one.
        platform !== "web" && platform,
    ]
        .filter(Boolean)
        .join(" · ");
};
