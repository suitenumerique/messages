import { Haptics, ImpactStyle } from "@capacitor/haptics";

const IMPACT_STYLES = {
  LIGHT: ImpactStyle.Light,
  MEDIUM: ImpactStyle.Medium,
} as const;

/**
 * Fire a haptic pulse: the native engines through the Capacitor plugin (iOS
 * has no Web Vibration API), the Web Vibration API in a browser. A platform
 * without either rejects; feedback is a nicety, so the rejection is dropped.
 *
 * @param style Impact strength — the gesture decides how firm the feedback
 * should be (a light tick when a swipe crosses its threshold, a medium thump
 * when a long press opens selection mode).
 */
export const triggerHaptic = (style: keyof typeof IMPACT_STYLES = "MEDIUM") => {
  void Haptics.impact({ style: IMPACT_STYLES[style] }).catch(() => {});
};

export default triggerHaptic;
