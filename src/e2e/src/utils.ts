/**
 * Utility functions for E2E tests.
 */

import { exec } from 'child_process';
import { STORAGE_STATE_PATH } from './constants';
import path from 'path';
import fs from 'fs';

/**
 * Execute a npm command in the e2e-runner container.
 */
async function runNpmCommand(command: string, args: string[] = [], timeout: number = 1000): Promise<string> {
  const commandArgs = [command, ...args].join(' ');

  const fullCommand = `npm run ${commandArgs}`;


  if (timeout) {
    await new Promise((resolve) => { setTimeout(resolve, timeout) });
  }

  return new Promise((resolve, reject) => {
    exec(fullCommand, (error, stdout) => {
      if (error) reject(error);
      else resolve(stdout);
    });
  });
}

/**
 * Reset the database by flushing all data (keeps schema) then
 * bootstrapping the demo data.
 */
export async function resetDatabase(): Promise<void> {
  await runNpmCommand('db:reset');
}

export const getStorageStatePath = (username: string): string => {
  return path.join(STORAGE_STATE_PATH, `user-${username}.json`);
};

/**
 * Helper function to get storage state path if it exists, otherwise undefined.
 * This allows setup projects to reuse existing authentication if available.
 */
export const getStorageStatePathIfExists = (username: string): string | undefined => {
  const path = getStorageStatePath(username);
  return fs.existsSync(path) ? path : undefined;
};

export const getMailboxEmail = (username: 'user' | 'mailbox_admin' | 'domain_admin' | 'super_admin' | 'shared', browserName?: 'chromium' | 'firefox' | 'webkit'): string => {
  if (browserName) return `${username}.e2e.${browserName}@example.local`;
  return `${username}.e2e@example.local`;
};
