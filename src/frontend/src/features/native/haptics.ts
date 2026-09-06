type CapacitorHaptics = { impact?: (options: { style: string }) => Promise<unknown> };

/**
 * Fire a haptic pulse when the platform exposes one: the Capacitor Haptics
 * plugin if the native app bundles it (covers iOS, which has no Web Vibration
 * API), otherwise the Web Vibration API (Android). Accessed off the global
 * Capacitor proxy so the build keeps working when the plugin isn't installed.
 *
 * @param fallbackMs Duration of the Web Vibration API fallback pulse.
 * @param style Capacitor impact style — the gesture decides how firm the
 * feedback should be (a light tick when a swipe crosses its threshold, a
 * medium thump when a long press opens selection mode).
 */
export const triggerHaptic = (fallbackMs: number, style: "LIGHT" | "MEDIUM" = "MEDIUM") => {
  const haptics = (
    globalThis as unknown as {
      Capacitor?: { Plugins?: { Haptics?: CapacitorHaptics } };
    }
  ).Capacitor?.Plugins?.Haptics;
  if (haptics?.impact) {
    void Promise.resolve(haptics.impact({ style })).catch(() => {});
    return;
  }
  navigator.vibrate?.(fallbackMs);
};

export default triggerHaptic;
