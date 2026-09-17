/**
 * The user dismissed the system browser before the login flow completed.
 * Kept free of any Capacitor import so the login code can tell it apart
 * from a failure without dragging the browser plugin along.
 */
export class AuthSessionCancelledError extends Error {
  constructor() {
    super("Authentication was cancelled.");
    this.name = "AuthSessionCancelledError";
  }
}
