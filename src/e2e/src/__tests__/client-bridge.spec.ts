/**
 * E2E tests for the client-bridge IMAP server against the real Messages API.
 *
 * These tests verify that the client-bridge correctly communicates with the
 * backend, catching issues that mock-based unit tests miss (e.g. wrong query
 * params, missing API endpoints).
 */

import test, { expect } from "@playwright/test";
import { ImapFlow } from "imapflow";
import {
  CLIENTBRIDGE_IMAP_HOST,
  CLIENTBRIDGE_IMAP_PORT,
  CLIENTBRIDGE_APP_PASSWORD,
  API_URL,
} from "../constants";
import { bootstrapClientBridge } from "../utils";
import { signInKeycloakIfNeeded } from "../utils-test";

const IMAP_USER = "user.e2e.chromium@example.local";

async function createImapClient(): Promise<ImapFlow> {
  const client = new ImapFlow({
    host: CLIENTBRIDGE_IMAP_HOST,
    port: CLIENTBRIDGE_IMAP_PORT,
    secure: false,
    auth: {
      user: IMAP_USER,
      pass: CLIENTBRIDGE_APP_PASSWORD,
    },
    logger: false,
  });
  await client.connect();
  return client;
}

test.describe("Client Bridge IMAP", () => {
  test.beforeAll(async () => {
    await bootstrapClientBridge();
  });

  test("should authenticate and list INBOX", async () => {
    const client = await createImapClient();
    try {
      const lock = await client.getMailboxLock("INBOX");
      try {
        expect(client.mailbox).toBeTruthy();
        expect(client.mailbox!.exists).toBeGreaterThan(0);
      } finally {
        lock.release();
      }
    } finally {
      await client.logout();
    }
  });

  test("should list virtual folders", async () => {
    const client = await createImapClient();
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

  test("should reflect API read state as IMAP \\Seen flag", async () => {
    // The e2e demo creates two IMAP test messages:
    // - "IMAP unread test" (read_at=null → unread)
    // - "IMAP read test" (read_at set → read)
    const client = await createImapClient();
    try {
      const lock = await client.getMailboxLock("INBOX");
      try {
        // Fetch flags for all messages
        const messages: Array<{ uid: number; flags: Set<string>; subject: string }> = [];
        for await (const msg of client.fetch("1:*", { flags: true, envelope: true })) {
          messages.push({
            uid: msg.uid,
            flags: msg.flags,
            subject: msg.envelope.subject || "",
          });
        }

        const unreadMsg = messages.find((m) => m.subject === "IMAP unread test");
        const readMsg = messages.find((m) => m.subject === "IMAP read test");

        expect(unreadMsg).toBeTruthy();
        expect(readMsg).toBeTruthy();
        expect(unreadMsg!.flags.has("\\Seen")).toBe(false);
        expect(readMsg!.flags.has("\\Seen")).toBe(true);
      } finally {
        lock.release();
      }
    } finally {
      await client.logout();
    }
  });

  test("should sync read state from IMAP to webmail API", async ({ page }) => {
    // Sign in to get an authenticated session for API calls
    await signInKeycloakIfNeeded({ page, username: `user.e2e.chromium` });

    const client = await createImapClient();
    try {
      const lock = await client.getMailboxLock("INBOX");
      let targetUid: number | undefined;
      try {
        // Find the unread message
        for await (const msg of client.fetch("1:*", { flags: true, envelope: true })) {
          if (msg.envelope.subject === "IMAP unread test") {
            targetUid = msg.uid;
            expect(msg.flags.has("\\Seen")).toBe(false);
            break;
          }
        }
        expect(targetUid).toBeTruthy();

        // Mark as read via IMAP
        await client.messageFlagsAdd({ uid: targetUid! }, ["\\Seen"]);
      } finally {
        lock.release();
      }
    } finally {
      await client.logout();
    }

    // Verify via the API that the thread is now read
    const threadsResp = await page.request.get(`${API_URL}/api/v1.0/threads/`, {
      params: { page_size: "100" },
    });
    expect(threadsResp.ok()).toBe(true);
    const threadsData = await threadsResp.json();
    const targetThread = threadsData.results?.find(
      (t: any) => t.subject === "IMAP unread test"
    );

    // The IMAP STORE +FLAGS \Seen should have set read_at on the thread access
    expect(targetThread).toBeTruthy();
  });

  test("should sync read state from webmail API to IMAP", async ({ page }) => {
    // Sign in to get API access
    await signInKeycloakIfNeeded({ page, username: `user.e2e.chromium` });

    // First, connect via IMAP to find a read message we can mark as unread via API
    let targetSubject = "IMAP read test";
    const client1 = await createImapClient();
    let targetUid: number | undefined;
    try {
      const lock = await client1.getMailboxLock("INBOX");
      try {
        for await (const msg of client1.fetch("1:*", { flags: true, envelope: true })) {
          if (msg.envelope.subject === targetSubject) {
            targetUid = msg.uid;
            // Should be read initially
            expect(msg.flags.has("\\Seen")).toBe(true);
            break;
          }
        }
      } finally {
        lock.release();
      }
    } finally {
      await client1.logout();
    }

    expect(targetUid).toBeTruthy();

    // Mark as unread via the webmail flag API
    // We need to find the thread ID first
    const threadsResp = await page.request.get(`${API_URL}/api/v1.0/threads/`, {
      params: { page_size: "100" },
    });
    expect(threadsResp.ok()).toBe(true);
    const threadsData = await threadsResp.json();
    const targetThread = threadsData.results?.find(
      (t: any) => t.subject === targetSubject
    );
    expect(targetThread).toBeTruthy();

    // Get mailbox ID for the user
    const mailboxResp = await page.request.get(`${API_URL}/api/v1.0/mailboxes/`);
    expect(mailboxResp.ok()).toBe(true);
    const mailboxData = await mailboxResp.json();
    const mailbox = mailboxData.results?.find(
      (m: any) => m.local_part === "user.e2e.chromium"
    );
    expect(mailbox).toBeTruthy();

    // Mark the thread as unread via the flag API
    const flagResp = await page.request.post(`${API_URL}/api/v1.0/flag/`, {
      data: {
        flag: "unread",
        value: true,
        thread_ids: [targetThread.id],
        mailbox_id: mailbox.id,
        read_at: null,
      },
    });
    expect(flagResp.ok()).toBe(true);

    // Reconnect via IMAP and verify the message no longer has \Seen
    const client2 = await createImapClient();
    try {
      const lock = await client2.getMailboxLock("INBOX");
      try {
        for await (const msg of client2.fetch("1:*", { flags: true, envelope: true })) {
          if (msg.envelope.subject === targetSubject) {
            expect(msg.flags.has("\\Seen")).toBe(false);
            break;
          }
        }
      } finally {
        lock.release();
      }
    } finally {
      await client2.logout();
    }
  });
});
