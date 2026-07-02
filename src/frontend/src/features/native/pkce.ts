/**
 * PKCE (RFC 7636) helpers used to bind the mobile session handoff: the app
 * generates a code verifier, sends its S256 challenge with the authenticate
 * request and reveals the verifier only on the token exchange.
 */

const CODE_VERIFIER_BYTE_LENGTH = 48;

const base64UrlEncode = (bytes: Uint8Array): string =>
  btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");

/**
 * Generate a random PKCE code verifier (64 base64url characters).
 */
export const generateCodeVerifier = (): string => {
  const bytes = crypto.getRandomValues(
    new Uint8Array(CODE_VERIFIER_BYTE_LENGTH),
  );
  return base64UrlEncode(bytes);
};

/**
 * Compute the S256 code challenge of a PKCE code verifier.
 *
 * @param codeVerifier The verifier returned by {@link generateCodeVerifier}
 * @returns The base64url-encoded SHA-256 digest of the verifier
 */
export const computeCodeChallenge = async (
  codeVerifier: string,
): Promise<string> => {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(codeVerifier),
  );
  return base64UrlEncode(new Uint8Array(digest));
};
