import { toNativeMediaUrl } from '@/features/native/media-url';
import { isNativePlatform } from '@/features/native/platform';
import MailHelper from '@/features/utils/mail-helper';

import { createNativeFileUrlResolver } from './utils';

vi.mock('@/features/native/media-url', () => ({
  toNativeMediaUrl: vi.fn((url: string) => `native:${url}`),
}));
vi.mock('@/features/native/platform', () => ({
  isNativePlatform: vi.fn(),
}));
vi.mock('@/features/utils/mail-helper', () => ({
  default: { extractBlobId: vi.fn() },
}));

const native = vi.mocked(isNativePlatform);
const extractBlobId = vi.mocked(MailHelper.extractBlobId);

const BLOB_URL = 'http://api.test/api/v1.0/blob/42/download/';
const REMOTE_URL = 'https://example.org/picture.png';

beforeEach(() => {
  vi.clearAllMocks();
  extractBlobId.mockImplementation((url) => (url === BLOB_URL ? '42' : null));
});

describe('createNativeFileUrlResolver', () => {
  it('adds no editor option on the web', () => {
    native.mockReturnValue(false);

    expect(createNativeFileUrlResolver()).toEqual({});
  });

  it('resolves blob download urls through the native media rewrite', async () => {
    native.mockReturnValue(true);
    const { resolveFileUrl } = createNativeFileUrlResolver();

    await expect(resolveFileUrl!(BLOB_URL)).resolves.toBe(`native:${BLOB_URL}`);
    expect(toNativeMediaUrl).toHaveBeenCalledWith(BLOB_URL);
  });

  it('leaves any other url to the WebView', async () => {
    native.mockReturnValue(true);
    const { resolveFileUrl } = createNativeFileUrlResolver();

    await expect(resolveFileUrl!(REMOTE_URL)).resolves.toBe(REMOTE_URL);
    expect(toNativeMediaUrl).not.toHaveBeenCalled();
  });
});
