import { Capacitor } from "@capacitor/core";

/**
 * Check whether the app is running inside a Capacitor native shell
 * (iOS or Android). Returns false in a regular web browser.
 */
export const isNativePlatform = (): boolean => Capacitor.isNativePlatform();
