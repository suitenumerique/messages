/**
 * E2E tests for the client-bridge IMAP server against the real Messages API.
 *
 * These tests verify that the client-bridge correctly communicates with the
 * backend, catching issues that mock-based unit tests miss (e.g. wrong query
 * params, missing API endpoints).
 */

import test, { expect, Page } from "@playwright/test";
import { ImapFlow } from "imapflow";
import {
  CLIENTBRIDGE_IMAP_HOST,
  CLIENTBRIDGE_IMAP_PORT,
  CLIENTBRIDGE_APP_PASSWORD,
  API_URL,
} from "../constants";
import { bootstrapClientBridge } from "../utils";
import { signInKeycloakIfNeeded } from "../utils-test";

// Each browser project uses its own mailbox, so parallel runs don't race.
const imapUser = (browserName: string) => `user.e2e.${browserName}@example.local`;

async function createImapClient(browserName: string): Promise<ImapFlow> {
  const client = new ImapFlow({
    host: CLIENTBRIDGE_IMAP_HOST,
    port: CLIENTBRIDGE_IMAP_PORT,
    secure: false,
    auth: {
      user: imapUser(browserName),
      pass: CLIENTBRIDGE_APP_PASSWORD,
    },
    logger: false,
  });
  await client.connect();
  return client;
}

/** Flags of the INBOX message with this subject, failing if it isn't there. */
async function inboxFlags(browserName: string, subject: string): Promise<Set<string>> {
  const client = await createImapClient(browserName);
  try {
    const lock = await client.getMailboxLock("INBOX");
    try {
      for await (const msg of client.fetch("1:*", { flags: true, envelope: true })) {
        if (msg.envelope?.subject === subject) {
          return msg.flags ?? new Set();
        }
      }
    } finally {
      lock.release();
    }
  } finally {
    await client.logout();
  }
  throw new Error(`"${subject}" not found in INBOX`);
}

/** The signed-in user's mailbox and CSRF token (needed by DRF on POST). */
async function apiContext(page: Page, browserName: string) {
  const meResponse = await page.request.get(`${API_URL}/api/v1.0/users/me/`);
  expect(meResponse.ok()).toBe(true);
  const { csrf_token: csrfToken } = await meResponse.json();

  const mailboxResponse = await page.request.get(`${API_URL}/api/v1.0/mailboxes/`);
  expect(mailboxResponse.ok()).toBe(true);
  const mailboxes: Array<{ id: string; email: string }> = await mailboxResponse.json();
  const mailbox = mailboxes.find((m) => m.email === imapUser(browserName));
  expect(mailbox).toBeTruthy();

  return { csrfToken: csrfToken as string, mailboxId: mailbox!.id };
}

/** The thread with this subject, with the read state of the mailbox. */
async function findThread(page: Page, mailboxId: string, subject: string) {
  const response = await page.request.get(`${API_URL}/api/v1.0/threads/`, {
    params: { mailbox_id: mailboxId, page_size: "100" },
  });
  expect(response.ok()).toBe(true);
  const data = await response.json();
  const thread = data.results?.find((t: { subject: string }) => t.subject === subject);
  expect(thread).toBeTruthy();
  return thread as { id: string; has_unread: boolean };
}

test.describe("Client Bridge IMAP", () => {
  test.beforeAll(async () => {
    await bootstrapClientBridge();
  });

  test("should authenticate and list INBOX", async ({ browserName }) => {
    const client = await createImapClient(browserName);
    try {
      const lock = await client.getMailboxLock("INBOX");
      try {
        expect(client.mailbox).toBeTruthy();
        expect(client.mailbox && client.mailbox.exists).toBeGreaterThan(0);
      } finally {
        lock.release();
      }
    } finally {
      await client.logout();
    }
  });

  test("should list virtual folders", async ({ browserName }) => {
    const client = await createImapClient(browserName);
    try {
      const folders = await client.list();
      const folderNames = folders.map((f) => f.path);
      expect(folderNames).toContain("INBOX");
      expect(folderNames).toContain("Sent");
      expect(folderNames).toContain("Trash");
      expect(folderNames).toContain("Drafts");
    } finally {
      await client.logout();
    }
  });

  test("should reflect API read state as IMAP \\Seen flag", async ({ browserName }) => {
    // e2e_clientbridge seeds "IMAP unread test" (unread) and "IMAP read test" (read)
    expect((await inboxFlags(browserName, "IMAP unread test")).has("\\Seen")).toBe(false);
    expect((await inboxFlags(browserName, "IMAP read test")).has("\\Seen")).toBe(true);
  });

  test("should sync read state from IMAP to webmail API", async ({ page, browserName }) => {
    await signInKeycloakIfNeeded({ page, username: `user.e2e.${browserName}` });
    const { mailboxId } = await apiContext(page, browserName);
    const subject = "IMAP unread test";
    expect((await findThread(page, mailboxId, subject)).has_unread).toBe(true);

    const client = await createImapClient(browserName);
    try {
      const lock = await client.getMailboxLock("INBOX");
      try {
        let targetUid: number | undefined;
        for await (const msg of client.fetch("1:*", { flags: true, envelope: true })) {
          if (msg.envelope?.subject === subject) {
            targetUid = msg.uid;
          }
        }
        expect(targetUid).toBeTruthy();
        // The bridge waits for the flag API call before answering STORE
        await client.messageFlagsAdd({ uid: targetUid! }, ["\\Seen"], { uid: true });
      } finally {
        lock.release();
      }
    } finally {
      await client.logout();
    }

    expect((await findThread(page, mailboxId, subject)).has_unread).toBe(false);
  });

  test("should sync read state from webmail API to IMAP", async ({ page, browserName }) => {
    await signInKeycloakIfNeeded({ page, username: `user.e2e.${browserName}` });
    const { csrfToken, mailboxId } = await apiContext(page, browserName);
    const subject = "IMAP read test";
    expect((await inboxFlags(browserName, subject)).has("\\Seen")).toBe(true);

    const thread = await findThread(page, mailboxId, subject);
    const flagResponse = await page.request.post(`${API_URL}/api/v1.0/flag/`, {
      data: {
        flag: "unread",
        value: true,
        thread_ids: [thread.id],
        mailbox_id: mailboxId,
        read_at: null,
      },
      headers: { "X-CSRFToken": csrfToken },
    });
    expect(flagResponse.ok()).toBe(true);

    // A new IMAP connection loads the folder again from the API
    expect((await inboxFlags(browserName, subject)).has("\\Seen")).toBe(false);
  });
});
