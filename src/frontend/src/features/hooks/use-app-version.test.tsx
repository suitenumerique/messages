/**
 * The version entry is the one place a user reads which build they run, so
 * these tests pin the two things that would make it lie: calling the native
 * plugin on a platform that does not implement it (the entry would vanish
 * behind an unhandled rejection), and reporting a native version on the web.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

vi.mock("@capacitor/core", async (importOriginal) => ({
    ...(await importOriginal<typeof import("@capacitor/core")>()),
    Capacitor: { getPlatform: vi.fn(), isNativePlatform: vi.fn() },
}));
vi.mock("@capacitor/app", () => ({
    App: { getInfo: vi.fn() },
}));

import { App } from "@capacitor/app";
import { Capacitor } from "@capacitor/core";

import { formatVersionReport, useAppVersion, type AppVersion } from "./use-app-version";

declare global {
    var IS_REACT_ACT_ENVIRONMENT: boolean;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

/** Mounts the hook and resolves with the value of its last render. */
const renderAppVersion = async (): Promise<AppVersion> => {
    let latest: AppVersion | undefined;
    const Probe = () => {
        latest = useAppVersion();
        return null;
    };

    await act(async () => {
        root.render(<Probe />);
    });

    return latest!;
};

describe("useAppVersion", () => {
    beforeEach(() => {
        vi.mocked(Capacitor.getPlatform).mockReturnValue("web");
        vi.mocked(Capacitor.isNativePlatform).mockReturnValue(false);
        vi.mocked(App.getInfo).mockResolvedValue({
            name: "ST Messages",
            id: "local.suitenumerique.messages",
            version: "1.2.0",
            build: "42",
        });
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(() => {
        act(() => root.unmount());
        container.remove();
        vi.clearAllMocks();
    });

    it("reports the web version only, and never calls the plugin, on the web", async () => {
        const version = await renderAppVersion();

        expect(version).toEqual({ web: "0.0.0-test", source: "test" });
        expect(App.getInfo).not.toHaveBeenCalled();
    });

    it("adds the installed app version on a native platform", async () => {
        vi.mocked(Capacitor.isNativePlatform).mockReturnValue(true);

        const version = await renderAppVersion();

        expect(version).toEqual({
            web: "0.0.0-test",
            source: "test",
            native: "1.2.0",
            nativeBuild: "42",
        });
    });

    it("keeps the web version when the native call fails", async () => {
        vi.mocked(Capacitor.isNativePlatform).mockReturnValue(true);
        vi.mocked(App.getInfo).mockRejectedValue(new Error("not implemented"));

        const version = await renderAppVersion();

        expect(version).toEqual({ web: "0.0.0-test", source: "test" });
    });
});

describe("formatVersionReport", () => {
    it("carries every number identifying a native build", () => {
        vi.mocked(Capacitor.getPlatform).mockReturnValue("ios");

        expect(
            formatVersionReport({
                web: "0.1.0",
                source: "a1b2c3d",
                native: "1.2.0",
                nativeBuild: "42",
            }),
        ).toBe("app 1.2.0 (42) · web 0.1.0 (a1b2c3d) · ios");
    });

    it("keeps only the web segment on the web", () => {
        vi.mocked(Capacitor.getPlatform).mockReturnValue("web");

        expect(formatVersionReport({ web: "0.1.0", source: "a1b2c3d" })).toBe(
            "web 0.1.0 (a1b2c3d)",
        );
    });
});
