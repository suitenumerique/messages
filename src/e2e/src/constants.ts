import path from 'path';

if (!process.env.FRONTEND_BASE_URL || !process.env.BACKEND_BASE_URL || !process.env.KEYCLOAK_BASE_URL) {
  throw new Error('FRONTEND_BASE_URL, BACKEND_BASE_URL and KEYCLOAK_BASE_URL must be set');
}

export const CLIENT_URL = process.env.FRONTEND_BASE_URL;
export const API_URL = process.env.BACKEND_BASE_URL;
export const AUTHENTICATION_URL = process.env.KEYCLOAK_BASE_URL;
export const STORAGE_STATE_PATH = path.join(__dirname, `./__tests__/.auth`);
export const FIXTURES_PATH = path.join(__dirname, `./fixtures`);

// Client-bridge (IMAP/SMTP) settings
export const CLIENTBRIDGE_IMAP_HOST = process.env.CLIENTBRIDGE_IMAP_HOST || 'client-bridge';
export const CLIENTBRIDGE_IMAP_PORT = parseInt(process.env.CLIENTBRIDGE_IMAP_PORT || '143', 10);
export const CLIENTBRIDGE_APP_PASSWORD = 'e2e-client-bridge-password';
