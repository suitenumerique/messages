import { vi } from 'vitest';
import MailHelper, { SUPPORTED_IMAP_DOMAINS, ATTACHMENT_SEPARATORS } from './mail-helper';
import DetectionMap from '@/features/i18n/attachments-detection-map.json';
import i18n from '@/features/i18n/initI18n';
import { getApiOrigin } from '@/features/api/utils';

vi.mock('./errors', () => ({ handle: vi.fn() }));

const withLanguage = (lng: string, fn: () => void) => {
  const previous = i18n.language;
  i18n.language = lng;
  try {
    fn();
  } finally {
    i18n.language = previous;
  }
};

describe('MailHelper', () => {
  describe('prefixSubjectIfNeeded', () => {
    it('should add prefix if not present', () => {
      const subject = 'Test Subject';
      const result = MailHelper.prefixSubjectIfNeeded(subject);
      expect(result).toBe('Re: Test Subject');
    });

    it('should not add prefix if already present', () => {
      const subject = 'Re: Test Subject';
      const result = MailHelper.prefixSubjectIfNeeded(subject);
      expect(result).toBe('Re: Test Subject');
    });

    it('should use custom prefix', () => {
      const subject = 'Re: Test Subject';
      const result = MailHelper.prefixSubjectIfNeeded(subject, 'Fwd:');
      expect(result).toBe('Fwd: Re: Test Subject');
    });
  });

  describe('parseRecipients', () => {
    it('should parse single recipient', () => {
      const recipients = 'test@example.com';
      const result = MailHelper.parseRecipients(recipients);
      expect(result).toEqual(['test@example.com']);
    });

    it('should parse multiple recipients', () => {
      const recipients = 'test1@example.com, test2@example.com';
      const result = MailHelper.parseRecipients(recipients);
      expect(result).toEqual(['test1@example.com', 'test2@example.com']);
    });

    it('should handle whitespace', () => {
      const recipients = ' test1@example.com ,  test2@example.com ';
      const result = MailHelper.parseRecipients(recipients);
      expect(result).toEqual(['test1@example.com', 'test2@example.com']);
    });
  });

  describe('areRecipientsValid', () => {
    it('should validate multiple valid emails', () => {
      const recipients = ['test1@example.com', 'test2@example.com'];
      const result = MailHelper.areRecipientsValid(recipients);
      expect(result).toBe(true);
    });

    it('should reject invalid emails', () => {
      const recipients = ['invalid-email', 'test@example.com'];
      const result = MailHelper.areRecipientsValid(recipients);
      expect(result).toBe(false);
    });

    it('should handle empty array when required', () => {
      const result = MailHelper.areRecipientsValid([], true);
      expect(result).toBe(false);
    });

    it('should handle empty array when not required', () => {
      const result = MailHelper.areRecipientsValid([], false);
      expect(result).toBe(true);
    });

    it('should handle undefined recipients when required', () => {
      const result = MailHelper.areRecipientsValid(undefined, true);
      expect(result).toBe(false);
    });

    it('should handle undefined recipients when not required', () => {
      const result = MailHelper.areRecipientsValid(undefined, false);
      expect(result).toBe(true);
    });

    it.each([
      'test@.com',
      'test@com',
      '@example.com',
      'test@example.',
      '.test@example.com',
      'test@example..com',
      'text@example_23.com'
    ])('should reject emails with invalid format (%s)', (email) => {
        const result = MailHelper.areRecipientsValid([email]);
        expect(result).toBe(false);
    });

    it.each([
      'test@example.com',
      'test.test@example.com',
      'test-test@example.com',
      'test_test@example.com',
      'test@example.co.uk',
      'test@sub.sub.example.com',
      'contact@42.com',
      'test@example-co-uk.com',
      'test123@example.com'
    ])('should accept emails with valid format (%s)', (email) => {
        const result = MailHelper.areRecipientsValid([email]);
        expect(result).toBe(true);
      });
  });

  describe('getDomainFromEmail', () => {
    it('should extract domain from valid email', () => {
      const email = 'test@example.com';
      const result = MailHelper.getDomainFromEmail(email);
      expect(result).toBe('example.com');
    });

    it('should return undefined for invalid email', () => {
      const email = 'invalid-email';
      const result = MailHelper.getDomainFromEmail(email);
      expect(result).toBeUndefined();
    });

    it('should handle email with subdomain', () => {
      const email = 'test@sub.example.com';
      const result = MailHelper.getDomainFromEmail(email);
      expect(result).toBe('sub.example.com');
    });
  });

  describe('getImapConfigFromEmail', () => {
    it('should support orange, wanadoo, gmail and yahoo domains', () => {
      expect(Array.from(SUPPORTED_IMAP_DOMAINS.keys())).toMatchInlineSnapshot(`
        [
          "orange.fr",
          "wanadoo.fr",
          "(gmail.com|googlemail.com)",
          "yahoo.(?:[a-z]{2,4}|[a-z]{2}.[a-z]{2})",
        ]
      `);
    });

    it.each(['orange.fr', 'wanadoo.fr', 'gmail.com', 'yahoo.fr', 'yahoo.co.uk'])('should return config for supported domain (%s)', (domain) => {
      const email = `test@${domain}`;
      const result = MailHelper.getImapConfigFromEmail(email);
      expect(result).not.toBeUndefined();
      expect(Object.keys(result!)).toMatchObject(['host', 'port', 'use_ssl']);
    });

    it('should return undefined for unsupported domain', () => {
      const email = 'test@example.com';
      const result = MailHelper.getImapConfigFromEmail(email);
      expect(result).toBeUndefined();
    });

    it('should return undefined for invalid email', () => {
      const email = 'invalid-email';
      const result = MailHelper.getImapConfigFromEmail(email);
      expect(result).toBeUndefined();
    });
  });

  describe('MailHelper.getAttachmentKeywords', () => {
  it('should extract all keywords from the detection map and normalize to lowercase', () => {
    const detectionMap = {
      en: {
        attachment: ["Attachment", "attached file"],
        abbreviations: ["Att.", "Enc."]
      },
      fr: {
        attachment: ["Pièce jointe"],
        abbreviations: ["PJ"]
      }
    };

    const keywords = MailHelper.getAttachmentKeywords(detectionMap);

    // Check if all expected keywords are present
    expect(keywords).toEqual(
      expect.arrayContaining([
        'attachment',
        'attached file',
        'att.',
        'enc.',
        'pièce jointe',
        'pj'
      ])
    );

    // No duplicates
    const uniqueKeywords = new Set(keywords);
    expect(uniqueKeywords.size).toBe(keywords.length);
  });
  });

  describe('MailHelper.areAttachmentsMentionedInDraft', () => {
    it('should return true if draft contains an attachment keyword (case insensitive)', () => {
      const draftText = 'Please find the ATTACHED file in this email.';
      const result = MailHelper.areAttachmentsMentionedInDraft(draftText);
      expect(result).toBe(true);
    });

    it('should return true if draft contains an attachment keyword in French', () => {
      const draftText = 'Vous trouverez la pièce jointe ci-dessous.';
      const result = MailHelper.areAttachmentsMentionedInDraft(draftText);
      expect(result).toBe(true);
    });

    it('should return false if no attachment keywords are present', () => {
      const draftText = 'Hello, how are you today?';
      const result = MailHelper.areAttachmentsMentionedInDraft(draftText);
      expect(result).toBe(false);
    });

    it('should handle empty safely', () => {
      expect(MailHelper.areAttachmentsMentionedInDraft('')).toBe(false);
    });

    it('should use regex patterns if present', () => {
      const draftText = 'I didn\'t include the document.';
      const result = MailHelper.areAttachmentsMentionedInDraft(draftText);
      expect(result).toBe(true);
    });
  });

  describe('MailHelper.attachDriveAttachmentsToDraft', () => {
    it('should attach drive attachments to draft', () => {
      const draft = 'Hello, how are you today?';
      const attachments = [
        { id: '1', name: 'test.pdf', url: 'https://example.com/test.pdf', type: 'application/pdf', size: 100, created_at: '2021-01-01' },
        { id: '2', name: 'test.docx', url: 'https://example.com/test.docx', type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', size: 200, created_at: '2021-01-02' }
      ];
      const result = MailHelper.attachDriveAttachmentsToDraft(draft, attachments);
      expect(result).toMatchInlineSnapshot(`"Hello, how are you today?---------- Drive attachments ----------[{"id":"1","name":"test.pdf","url":"https://example.com/test.pdf","type":"application/pdf","size":100,"created_at":"2021-01-01"},{"id":"2","name":"test.docx","url":"https://example.com/test.docx","type":"application/vnd.openxmlformats-officedocument.wordprocessingml.document","size":200,"created_at":"2021-01-02"}]"`);
    });

    it('should return original draft if no attachments', () => {
      const draft = 'Hello, how are you today?';
      const result = MailHelper.attachDriveAttachmentsToDraft(draft, []);
      expect(result).toBe('Hello, how are you today?');
    });

    it('should handle empty draft', () => {
      const attachments = [
        { id: '1', name: 'test.pdf', url: 'https://example.com/test.pdf', type: 'application/pdf', size: 100, created_at: '2021-01-01' }
      ];
      const result = MailHelper.attachDriveAttachmentsToDraft('', attachments);
      expect(result).toMatchInlineSnapshot(`"---------- Drive attachments ----------[{"id":"1","name":"test.pdf","url":"https://example.com/test.pdf","type":"application/pdf","size":100,"created_at":"2021-01-01"}]"`);
    });

    it('should handle undefined draft', () => {
      const attachments = [
        { id: '1', name: 'test.pdf', url: 'https://example.com/test.pdf', type: 'application/pdf', size: 100, created_at: '2021-01-01' }
      ];
      const result = MailHelper.attachDriveAttachmentsToDraft(undefined, attachments);
      expect(result).toMatchInlineSnapshot(`"---------- Drive attachments ----------[{"id":"1","name":"test.pdf","url":"https://example.com/test.pdf","type":"application/pdf","size":100,"created_at":"2021-01-01"}]"`);
    });

    it('should handle undefined attachments', () => {
      const draft = 'Hello, how are you today?';
      const result = MailHelper.attachDriveAttachmentsToDraft(draft, undefined);
      expect(result).toBe('Hello, how are you today?');
    });
  });

  describe('MailHelper.attachDriveAttachmentsToTextBody', () => {
    it('should attach drive attachments to text body as markdown links', () => {
      const textBody = 'Hello, how are you today?';
      const attachments = [
        { id: '1', name: 'test.pdf', url: 'https://example.com/test.pdf', type: 'application/pdf', size: 100, created_at: '2021-01-01' },
        { id: '2', name: 'test.docx', url: 'https://example.com/test.docx', type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', size: 200, created_at: '2021-01-02' }
      ];
      const result = MailHelper.attachDriveAttachmentsToTextBody(textBody, attachments);
      expect(result).toMatchInlineSnapshot(`
        "Hello, how are you today?
        ---------- Drive attachments ----------
        - [test.pdf](https://example.com/test.pdf)
        - [test.docx](https://example.com/test.docx)

        "
      `)
    });

    it('should return original text body if no attachments', () => {
      const textBody = 'Hello, how are you today?';
      const result = MailHelper.attachDriveAttachmentsToTextBody(textBody, []);
      expect(result).toBe('Hello, how are you today?');
    });

    it('should handle empty text body', () => {
      const attachments = [
        { id: '1', name: 'test.pdf', url: 'https://example.com/test.pdf', type: 'application/pdf', size: 100, created_at: '2021-01-01' }
      ];
      const result = MailHelper.attachDriveAttachmentsToTextBody('', attachments);
      expect(result).toMatchInlineSnapshot(`
        "
        ---------- Drive attachments ----------
        - [test.pdf](https://example.com/test.pdf)

        "
      `);
    });

    it('should handle undefined text body', () => {
      const attachments = [
        { id: '1', name: 'test.pdf', url: 'https://example.com/test.pdf', type: 'application/pdf', size: 100, created_at: '2021-01-01' }
      ];
      const result = MailHelper.attachDriveAttachmentsToTextBody(undefined, attachments);
      expect(result).toMatchInlineSnapshot(`
        "
        ---------- Drive attachments ----------
        - [test.pdf](https://example.com/test.pdf)

        "
      `);
    });

    it('should handle undefined attachments', () => {
      const textBody = 'Hello, how are you today?';
      const result = MailHelper.attachDriveAttachmentsToTextBody(textBody, undefined);
      expect(result).toBe('Hello, how are you today?');
    });
  });

  describe('MailHelper.attachDriveAttachmentsToHtmlBody', () => {
    it('should attach drive attachments to html body as html links with data attributes', () => {
      const htmlBody = '<h1>Hello, how are you today?</h1>';
      const attachments = [
        { id: '1', name: 'test.pdf', url: 'https://example.com/test.pdf', type: 'application/pdf', size: 100, created_at: '2021-01-01' },
        { id: '2', name: 'test.docx', url: 'https://example.com/test.docx', type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', size: 200, created_at: '2021-01-02' }
      ];
      const result = MailHelper.attachDriveAttachmentsToHtmlBody(htmlBody, attachments);
      expect(result).toMatchInlineSnapshot(`
        "<h1>Hello, how are you today?</h1>
        ---------- Drive attachments ----------
        <ul><li><a class="drive-attachment" href="https://example.com/test.pdf" data-id="1" data-name="test.pdf" data-type="application/pdf" data-size="100" data-created_at="2021-01-01">test.pdf</a></li><li><a class="drive-attachment" href="https://example.com/test.docx" data-id="2" data-name="test.docx" data-type="application/vnd.openxmlformats-officedocument.wordprocessingml.document" data-size="200" data-created_at="2021-01-02">test.docx</a></li></ul>

        "
      `);
    });

    it('should return original html body if no attachments', () => {
      const htmlBody = '<h1>Hello, how are you today?</h1>';
      const result = MailHelper.attachDriveAttachmentsToHtmlBody(htmlBody, []);
      expect(result).toBe('<h1>Hello, how are you today?</h1>');
    });

    it('should handle empty html body', () => {
      const attachments = [
        { id: '1', name: 'test.pdf', url: 'https://example.com/test.pdf', type: 'application/pdf', size: 100, created_at: '2021-01-01' }
      ];
      const result = MailHelper.attachDriveAttachmentsToHtmlBody('', attachments);
      expect(result).toMatchInlineSnapshot(`
        "
        ---------- Drive attachments ----------
        <ul><li><a class="drive-attachment" href="https://example.com/test.pdf" data-id="1" data-name="test.pdf" data-type="application/pdf" data-size="100" data-created_at="2021-01-01">test.pdf</a></li></ul>

        "
      `);
    });

    it('should handle undefined html body', () => {
      const attachments = [
        { id: '1', name: 'test.pdf', url: 'https://example.com/test.pdf', type: 'application/pdf', size: 100, created_at: '2021-01-01' }
      ];
      const result = MailHelper.attachDriveAttachmentsToHtmlBody(undefined, attachments);
      expect(result).toMatchInlineSnapshot(`
        "
        ---------- Drive attachments ----------
        <ul><li><a class="drive-attachment" href="https://example.com/test.pdf" data-id="1" data-name="test.pdf" data-type="application/pdf" data-size="100" data-created_at="2021-01-01">test.pdf</a></li></ul>

        "
      `);
    });

    it('should handle undefined attachments', () => {
      const htmlBody = '<h1>Hello, how are you today?</h1>';
      const result = MailHelper.attachDriveAttachmentsToHtmlBody(htmlBody, undefined);
      expect(result).toBe('<h1>Hello, how are you today?</h1>');
    });

    it('should escape malicious strings in attachment attributes', () => {
      const htmlBody = '<h1>Hello</h1>';
      const attachments = [
        { id: '1"><script>alert("xss")</script>', name: '<img src=x onerror=alert(1)>', url: 'https://example.com/"><script>alert(1)</script>', type: 'application/pdf', size: 100, created_at: '2021-01-01' }
      ];
      const result = MailHelper.attachDriveAttachmentsToHtmlBody(htmlBody, attachments);
      // No unescaped script or img tags
      expect(result).not.toContain('<script>');
      expect(result).not.toContain('<img ');
      // Malicious name is properly escaped in both attribute and text content
      expect(result).toContain('&lt;img src=x onerror=alert(1)&gt;');
    });
  });

  describe('MailHelper.extractDriveAttachmentsFromDraft', () => {
    it('should extract drive attachments from draft', () => {
      const draft = 'Hello, how are you today?---------- Drive attachments ----------[{"id":"1","name":"test.pdf","url":"https://example.com/test.pdf","type":"application/pdf","size":100,"created_at":"2021-01-01"},{"id":"2","name":"test.docx","url":"https://example.com/test.docx","type":"application/vnd.openxmlformats-officedocument.wordprocessingml.document","size":200,"created_at":"2021-01-02"}]';
      const result = MailHelper.extractDriveAttachmentsFromDraft(draft);
      expect(result).toEqual([
        'Hello, how are you today?',
        [
          { id: '1', name: 'test.pdf', url: 'https://example.com/test.pdf', type: 'application/pdf', size: 100, created_at: '2021-01-01' },
          { id: '2', name: 'test.docx', url: 'https://example.com/test.docx', type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', size: 200, created_at: '2021-01-02' }
        ]
      ]);
    });

    it('should handle draft without attachments', () => {
      const draft = 'Hello, how are you today?';
      const result = MailHelper.extractDriveAttachmentsFromDraft(draft);
      expect(result).toEqual(['Hello, how are you today?', []]);
    });

    it('should handle empty draft', () => {
      const result = MailHelper.extractDriveAttachmentsFromDraft('');
      expect(result).toEqual(['', []]);
    });

    it('should handle undefined draft', () => {
      const result = MailHelper.extractDriveAttachmentsFromDraft(undefined);
      expect(result).toEqual(['', []]);
    });

    it('should handle draft with invalid JSON attachments', () => {
      const draft = 'Hello, how are you today?---------- Drive attachments ----------invalid json';
      const result = MailHelper.extractDriveAttachmentsFromDraft(draft);
      expect(result).toEqual(['Hello, how are you today?', []]);
    });

    it('should handle draft with legacy separator', () => {
      // Add a legacy separator to the ATTACHMENT_SEPARATORS array just for this test
      ATTACHMENT_SEPARATORS.unshift('---------- Drive legacy sep ----------');
      try {
        const draft = 'Hello, how are you today?---------- Drive legacy sep ----------[{"id":"1","name":"test.pdf","url":"https://example.com/test.pdf","type":"application/pdf","size":100,"created_at":"2021-01-01"}]';
        const result = MailHelper.extractDriveAttachmentsFromDraft(draft);
        expect(result).toEqual([
          'Hello, how are you today?',
          [
            { id: '1', name: 'test.pdf', url: 'https://example.com/test.pdf', type: 'application/pdf', size: 100, created_at: '2021-01-01' }
          ]
        ]);
      } finally {
        ATTACHMENT_SEPARATORS.shift();
      }
    });
  });

  describe('MailHelper.extractDriveAttachmentsFromTextBody', () => {
    it('should extract drive attachments from text body', () => {
      const text = MailHelper.attachDriveAttachmentsToTextBody(
        'Hello, how are you today?',
        [
          { id: '1', name: 'test.pdf', url: 'https://example.com/test.pdf', type: 'application/pdf', size: 100, created_at: '2021-01-01' },
          { id: '2', name: 'test.docx', url: 'https://example.com/test.docx', type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', size: 200, created_at: '2021-01-02' }
        ]
      );
      const result = MailHelper.extractDriveAttachmentsFromTextBody(text);
      expect(result).toEqual(
        ['Hello, how are you today?',
          [
            { name: 'test.pdf', url: 'https://example.com/test.pdf' },
            { name: 'test.docx', url: 'https://example.com/test.docx' }
          ]
        ]);
    });

    it('should handle text body without attachments', () => {
      const text = 'Hello, how are you today?';
      const result = MailHelper.extractDriveAttachmentsFromTextBody(text);
      expect(result).toEqual(['Hello, how are you today?', []]);
    });

    it('should handle empty text body', () => {
      const result = MailHelper.extractDriveAttachmentsFromTextBody('');
      expect(result).toEqual(['', []]);
    });

    it('should handle undefined text body', () => {
      const result = MailHelper.extractDriveAttachmentsFromTextBody(undefined);
      expect(result).toEqual(['', []]);
    });

    it('should handle text body with legacy separator', () => {
      // Add a legacy separator to the ATTACHMENT_SEPARATORS array just for this test
      ATTACHMENT_SEPARATORS.unshift('---------- Drive legacy sep ----------');
      try {
        const text = `Hello, how are you today?
---------- Drive legacy sep ----------
- [test.pdf](https://example.com/test.pdf)
- [test.docx](https://example.com/test.docx)

`;
        const result = MailHelper.extractDriveAttachmentsFromTextBody(text);
        expect(result).toEqual([
          'Hello, how are you today?',
          [
            { name: 'test.pdf', url: 'https://example.com/test.pdf' },
            { name: 'test.docx', url: 'https://example.com/test.docx' }
          ]
        ]);
      } finally {
        ATTACHMENT_SEPARATORS.shift();
      }
    });

    it('should handle malformed markdown links', () => {
      const text = `Hello, how are you today?
---------- Drive attachments ----------
- [test.pdf](https://example.com/test.pdf)
- invalid markdown link
- [test.docx](https://example.com/test.docx)

`;
      const result = MailHelper.extractDriveAttachmentsFromTextBody(text);
      expect(result).toEqual([
        'Hello, how are you today?',
        [
          { name: 'test.pdf', url: 'https://example.com/test.pdf' },
          { name: 'test.docx', url: 'https://example.com/test.docx' }
        ]
      ]);
    });

    it('should handle empty attachment section', () => {
      const text = `Hello, how are you today?
---------- Drive attachments ----------


`;
      const result = MailHelper.extractDriveAttachmentsFromTextBody(text);
      expect(result).toEqual(['Hello, how are you today?', []]);
    });

    it('should handle single attachment', () => {
      const text = `Hello, how are you today?
---------- Drive attachments ----------
- [single.pdf](https://example.com/single.pdf)

`;
      const result = MailHelper.extractDriveAttachmentsFromTextBody(text);
      expect(result).toEqual([
        'Hello, how are you today?',
        [
          { name: 'single.pdf', url: 'https://example.com/single.pdf' }
        ]
      ]);
    });

    it('should handle attachments with special characters in names', () => {
      const text = `Hello, how are you today?
---------- Drive attachments ----------
- [test file (1).pdf](https://example.com/test%20file%20(1).pdf)
- [document-with-dash.docx](https://example.com/document-with-dash.docx)

`;
      const result = MailHelper.extractDriveAttachmentsFromTextBody(text);
      expect(result).toEqual([
        'Hello, how are you today?',
        [
          { name: 'test file (1).pdf', url: 'https://example.com/test%20file%20(1).pdf' },
          { name: 'document-with-dash.docx', url: 'https://example.com/document-with-dash.docx' }
        ]
      ]);
    });
  });

  describe('MailHelper.extractDriveAttachmentsFromHtmlBody', () => {
    it('should extract drive attachments from html body', () => {
      const html = MailHelper.attachDriveAttachmentsToHtmlBody(
        '<h1>Hello, how are you today?</h1>',
        [
          { id: '1', name: 'test.pdf', url: 'https://example.com/test.pdf', type: 'application/pdf', size: 100, created_at: '2021-01-01' },
          { id: '2', name: 'test.docx', url: 'https://example.com/test.docx', type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', size: 200, created_at: '2021-01-02' }
        ]
      );
      const result = MailHelper.extractDriveAttachmentsFromHtmlBody(html);
      expect(result).toEqual(
        ['<h1>Hello, how are you today?</h1>',
          [
            { id: '1', name: 'test.pdf', url: 'https://example.com/test.pdf', type: 'application/pdf', size: 100, created_at: '2021-01-01' },
            { id: '2', name: 'test.docx', url: 'https://example.com/test.docx', type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', size: 200, created_at: '2021-01-02' }
          ]
        ]);
    });

    it('should handle html body without attachments', () => {
      const html = '<h1>Hello, how are you today?</h1>';
      const result = MailHelper.extractDriveAttachmentsFromHtmlBody(html);
      expect(result).toEqual(['<h1>Hello, how are you today?</h1>', []]);
    });

    it('should handle empty html body', () => {
      const result = MailHelper.extractDriveAttachmentsFromHtmlBody('');
      expect(result).toEqual(['', []]);
    });

    it('should handle undefined html body', () => {
      const result = MailHelper.extractDriveAttachmentsFromHtmlBody(undefined);
      expect(result).toEqual(['', []]);
    });

    it('should handle html body with legacy separator', () => {
      // Add a legacy separator to the ATTACHMENT_SEPARATORS array just for this test
      ATTACHMENT_SEPARATORS.unshift('---------- Drive legacy sep ----------');
      try {
        const html = `<h1>Hello, how are you today?</h1>
---------- Drive legacy sep ----------
<ul>
<li>
<a class="drive-attachment" href="https://example.com/test.pdf" data-id="1" data-name="test.pdf" data-type="application/pdf" data-size="100" data-created_at="2021-01-01">test.pdf</a>
</li>
</ul>

`;
        const result = MailHelper.extractDriveAttachmentsFromHtmlBody(html);
        expect(result).toEqual([
          '<h1>Hello, how are you today?</h1>',
          [
            { id: '1', name: 'test.pdf', url: 'https://example.com/test.pdf', type: 'application/pdf', size: 100, created_at: '2021-01-01' }
          ]
        ]);
      } finally {
        ATTACHMENT_SEPARATORS.shift();
      }
    });

    it('should handle single attachment', () => {
      const html = `<h1>Hello, how are you today?</h1>
---------- Drive attachments ----------
<ul>
<li>
<a class="drive-attachment" href="https://example.com/single.pdf" data-id="1" data-name="single.pdf" data-type="application/pdf" data-size="100" data-created_at="2021-01-01">single.pdf</a>
</li>
</ul>

`;
      const result = MailHelper.extractDriveAttachmentsFromHtmlBody(html);
      expect(result).toEqual([
        '<h1>Hello, how are you today?</h1>',
        [
          { id: '1', name: 'single.pdf', url: 'https://example.com/single.pdf', type: 'application/pdf', size: 100, created_at: '2021-01-01' }
        ]
      ]);
    });

    it('should handle attachments with missing optional data attributes', () => {
      const html = `<h1>Hello, how are you today?</h1>
---------- Drive attachments ----------
<ul>
<li>
<a class="drive-attachment" href="https://example.com/test.pdf" data-id="1" data-name="test.pdf">test.pdf</a>
</li>
</ul>

`;
      const result = MailHelper.extractDriveAttachmentsFromHtmlBody(html);
      expect(result).toEqual([
        '<h1>Hello, how are you today?</h1>',
        [
          { id: '1', name: 'test.pdf', url: 'https://example.com/test.pdf', type: 'application/octet-stream', size: 0, created_at: '' }
        ]
      ]);
    });

    it('should handle malformed anchor elements', () => {
      const html = `<h1>Hello, how are you today?</h1>
---------- Drive attachments ----------
<ul>
<li>
<a class="drive-attachment" href="https://example.com/test.pdf" data-id="1" data-name="test.pdf" data-type="application/pdf" data-size="100" data-created_at="2021-01-01">test.pdf</a>
</li>
<li>
<a href="https://example.com/invalid.pdf">invalid.pdf</a>
</li>
<li>
<a class="drive-attachment" href="https://example.com/valid.docx" data-id="2" data-name="valid.docx" data-type="application/vnd.openxmlformats-officedocument.wordprocessingml.document" data-size="200" data-created_at="2021-01-02">valid.docx</a>
</li>
</ul>

`;
      const result = MailHelper.extractDriveAttachmentsFromHtmlBody(html);
      expect(result).toEqual([
        '<h1>Hello, how are you today?</h1>',
        [
          { id: '1', name: 'test.pdf', url: 'https://example.com/test.pdf', type: 'application/pdf', size: 100, created_at: '2021-01-01' },
          { id: '2', name: 'valid.docx', url: 'https://example.com/valid.docx', type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', size: 200, created_at: '2021-01-02' }
        ]
      ]);
    });

    it('should handle empty attachment section', () => {
      const html = `<h1>Hello, how are you today?</h1>
---------- Drive attachments ----------
<ul>

</ul>


`;
      const result = MailHelper.extractDriveAttachmentsFromHtmlBody(html);
      expect(result).toEqual(['<h1>Hello, how are you today?</h1>', []]);
    });

    it('should handle attachments with special characters in data attributes', () => {
      const html = `<h1>Hello, how are you today?</h1>
---------- Drive attachments ----------
<ul>
<li>
<a class="drive-attachment" href="https://example.com/test%20file%20(1).pdf" data-id="test-id-1" data-name="test file (1).pdf" data-type="application/pdf" data-size="100" data-created_at="2021-01-01T10:30:00Z">test file (1).pdf</a>
</li>
</ul>

`;
      const result = MailHelper.extractDriveAttachmentsFromHtmlBody(html);
      expect(result).toEqual([
        '<h1>Hello, how are you today?</h1>',
        [
          { id: 'test-id-1', name: 'test file (1).pdf', url: 'https://example.com/test%20file%20(1).pdf', type: 'application/pdf', size: 100, created_at: '2021-01-01T10:30:00Z' }
        ]
      ]);
    });

    it('should decode HTML entities in extracted attribute values (round-trip with &)', () => {
      const htmlBody = '<h1>Hello</h1>';
      const attachments = [
        { id: '1', name: 'report&summary.pdf', url: 'https://example.com/file?a=1&b=2', type: 'application/pdf', size: 100, created_at: '2021-01-01' }
      ];
      const html = MailHelper.attachDriveAttachmentsToHtmlBody(htmlBody, attachments);
      const [body, extracted] = MailHelper.extractDriveAttachmentsFromHtmlBody(html);
      expect(body).toBe('<h1>Hello</h1>');
      expect(extracted).toHaveLength(1);
      expect(extracted[0].name).toBe('report&summary.pdf');
      expect(extracted[0].url).toBe('https://example.com/file?a=1&b=2');
    });

    it('should decode HTML entities in extracted attribute values (round-trip with special chars)', () => {
      const htmlBody = '<h1>Hello</h1>';
      const attachments = [
        { id: '1', name: 'file<2>.pdf', url: 'https://example.com/file?q="test"&x=1', type: 'text/plain', size: 50, created_at: '2021-06-15' }
      ];
      const html = MailHelper.attachDriveAttachmentsToHtmlBody(htmlBody, attachments);
      const [body, extracted] = MailHelper.extractDriveAttachmentsFromHtmlBody(html);
      expect(body).toBe('<h1>Hello</h1>');
      expect(extracted).toHaveLength(1);
      expect(extracted[0].name).toBe('file<2>.pdf');
      expect(extracted[0].type).toBe('text/plain');
    });

    it('should handle anchor elements with missing required attributes', () => {
      const html = `<h1>Hello, how are you today?</h1>
---------- Drive attachments ----------
<ul>
<li>
<a class="drive-attachment" href="https://example.com/test.pdf">test.pdf</a>
</li>
<li>
<a class="drive-attachment" data-id="1" data-name="test2.pdf">test2.pdf</a>
</li>
<li>
<a class="drive-attachment" href="https://example.com/valid.pdf" data-id="1" data-name="valid.pdf" data-type="application/pdf" data-size="100" data-created_at="2021-01-01">valid.pdf</a>
</li>
</ul>

`;
      const result = MailHelper.extractDriveAttachmentsFromHtmlBody(html);
      expect(result).toEqual([
        '<h1>Hello, how are you today?</h1>',
        [
          { id: '1', name: 'valid.pdf', url: 'https://example.com/valid.pdf', type: 'application/pdf', size: 100, created_at: '2021-01-01' }
        ]
      ]);
    });
  });

  describe('replaceBlobUrlsWithCid', () => {
    it('should replace blob download URLs with cid: references', () => {
      const html = '<p>Hello</p><img src="https://localhost:8000/api/v1.0/blob/a1b2c3d4-e5f6-7890-abcd-ef1234567890/download/" />';
      const result = MailHelper.replaceBlobUrlsWithCid(html);
      expect(result).toBe('<p>Hello</p><img src="cid:a1b2c3d4-e5f6-7890-abcd-ef1234567890" />');
    });

    it('should replace multiple blob URLs', () => {
      const html = '<img src="https://example.com/api/v1.0/blob/aaaa-bbbb/download/" /><img src="https://example.com/api/v1.0/blob/cccc-dddd/download/" />';
      const result = MailHelper.replaceBlobUrlsWithCid(html);
      expect(result).toBe('<img src="cid:aaaa-bbbb" /><img src="cid:cccc-dddd" />');
    });

    it('should handle relative blob URLs (without origin)', () => {
      const html = '<img src="/api/v1.0/blob/a1b2c3d4-e5f6-7890-abcd-ef1234567890/download/" />';
      const result = MailHelper.replaceBlobUrlsWithCid(html);
      expect(result).toBe('<img src="cid:a1b2c3d4-e5f6-7890-abcd-ef1234567890" />');
    });

    it('should return html unchanged when no blob URLs are present', () => {
      const html = '<p>Hello World</p><img src="https://example.com/image.png" />';
      const result = MailHelper.replaceBlobUrlsWithCid(html);
      expect(result).toBe(html);
    });

    it('should handle empty string', () => {
      expect(MailHelper.replaceBlobUrlsWithCid('')).toBe('');
    });
  });

  describe('extractBlobId', () => {
    const blobId = 'a1b2c3d4-e5f6-7890-abcd-ef1234567890';

    it('should read the id from an absolute blob URL on the API origin', () => {
      expect(
        MailHelper.extractBlobId(`${getApiOrigin()}/api/v1.0/blob/${blobId}/download/`),
      ).toBe(blobId);
    });

    it('should read the id from a relative blob URL', () => {
      expect(MailHelper.extractBlobId(`/api/v1.0/blob/${blobId}/download/`)).toBe(blobId);
    });

    it('should read the id from a URL on the configured API origin', () => {
      vi.stubEnv('NEXT_PUBLIC_API_ORIGIN', 'https://api.example.test');
      try {
        expect(
          MailHelper.extractBlobId(`https://api.example.test/api/v1.0/blob/${blobId}/download/`),
        ).toBe(blobId);
        // The window origin is no longer the API: it must stop being accepted.
        expect(
          MailHelper.extractBlobId(`${window.location.origin}/api/v1.0/blob/${blobId}/download/`),
        ).toBeNull();
      } finally {
        vi.unstubAllEnvs();
      }
    });

    it.each([
      ['a remote image', 'https://example.com/photo.png'],
      ['a data URI', 'data:image/png;base64,iVBORw0KGgo='],
      ['an object URL', 'blob:http://localhost:8900/8f3b-4a1c'],
      ['an empty string', ''],
    ])('should return null for %s', (_label, url) => {
      expect(MailHelper.extractBlobId(url)).toBeNull();
    });

    it.each([
      // Anchored matching: only the URL we built ourselves designates an attachment.
      ['a URL that merely embeds a blob path', `https://evil.test/redirect?to=/api/v1.0/blob/${blobId}/download/`],
      ['an arbitrary origin', `https://cdn.example/api/v1.0/blob/${blobId}/download/`],
      ['a trailing path segment', `${getApiOrigin()}/api/v1.0/blob/${blobId}/download/extra`],
      ['a trailing query string', `${getApiOrigin()}/api/v1.0/blob/${blobId}/download/?x=1`],
    ])('should return null for %s', (_label, url) => {
      expect(MailHelper.extractBlobId(url)).toBeNull();
    });
  });

  describe('dataUrlToFile', () => {
    it('should convert a valid PNG data URL to a File', () => {
      // 1x1 red PNG as base64
      const base64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg==';
      const dataUrl = `data:image/png;base64,${base64}`;
      const file = MailHelper.dataUrlToFile(dataUrl, 'test.png');

      expect(file).not.toBeNull();
      expect(file!.name).toBe('test.png');
      expect(file!.type).toBe('image/png');
      expect(file!.size).toBeGreaterThan(0);
    });

    it('should convert a valid JPEG data URL to a File', () => {
      // Minimal valid JPEG (SOI + APP0 + EOI markers)
      const base64 = '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/xAAUAQEAAAAAAAAAAAAAAAAAAAAA/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAwDAQACEQMRAD8AKwA//9k=';
      const dataUrl = `data:image/jpeg;base64,${base64}`;
      const file = MailHelper.dataUrlToFile(dataUrl, 'photo.jpg');

      expect(file).not.toBeNull();
      expect(file!.name).toBe('photo.jpg');
      expect(file!.type).toBe('image/jpeg');
    });

    it('should return null for a non-data URL', () => {
      const file = MailHelper.dataUrlToFile('https://example.com/image.png', 'test.png');
      expect(file).toBeNull();
    });

    it('should return null for a non-image data URL', () => {
      const file = MailHelper.dataUrlToFile('data:text/plain;base64,SGVsbG8=', 'test.txt');
      expect(file).toBeNull();
    });

    it('should return null for a malformed data URL', () => {
      const file = MailHelper.dataUrlToFile('data:image/png;base64', 'test.png');
      expect(file).toBeNull();
    });

    it('should return null for invalid base64 content', () => {
      const file = MailHelper.dataUrlToFile('data:image/png;base64,!!!invalid!!!', 'test.png');
      expect(file).toBeNull();
    });

    it('should return null for empty string', () => {
      const file = MailHelper.dataUrlToFile('', 'test.png');
      expect(file).toBeNull();
    });
  });

  describe('DetectionMap', () => {
    it('should not have invalid regex patterns', () => {
      // A test guard to ensure that the detection map does not contain malformed regex patterns
      const regexPatterns = MailHelper.getAttachmentKeywords(DetectionMap).filter((pattern) => pattern.startsWith('/') && pattern.endsWith('/'));
      for (const pattern of regexPatterns) {
        expect(() => new RegExp(pattern.slice(1, -1), 'i')).not.toThrowError();
      }
    });
  });

  describe('localized attachment separator', () => {
    const attachments = [
      { id: '1', name: 'test.pdf', url: 'https://example.com/test.pdf', type: 'application/pdf', size: 100, created_at: '2021-01-01' }
    ];

    it('should use the French separator when i18n language is fr-FR', () => {
      withLanguage('fr-FR', () => {
        const draft = MailHelper.attachDriveAttachmentsToDraft('Bonjour', attachments);
        expect(draft).toContain('---------- Fichiers joints ----------');
        expect(draft).not.toContain('---------- Drive attachments ----------');
      });
    });

    it('should use the Dutch separator when i18n language is nl-NL', () => {
      withLanguage('nl-NL', () => {
        const text = MailHelper.attachDriveAttachmentsToTextBody('Hallo', attachments);
        expect(text).toContain('---------- Drive-bijlagen ----------');
      });
    });

    it('should use the English separator when i18n language is en-US', () => {
      withLanguage('en-US', () => {
        const html = MailHelper.attachDriveAttachmentsToHtmlBody('<p>Hi</p>', attachments);
        expect(html).toContain('---------- Drive attachments ----------');
      });
    });

    it('should fall back to the legacy English separator for an unknown language', () => {
      withLanguage('xx-XX', () => {
        const draft = MailHelper.attachDriveAttachmentsToDraft('Hello', attachments);
        expect(draft).toContain('---------- Drive attachments ----------');
      });
    });

    it('should round-trip a French-localized text body regardless of the reader language', () => {
      let text = '';
      withLanguage('fr-FR', () => {
        text = MailHelper.attachDriveAttachmentsToTextBody('Bonjour', attachments);
      });
      expect(text).toContain('---------- Fichiers joints ----------');

      withLanguage('en-US', () => {
        const result = MailHelper.extractDriveAttachmentsFromTextBody(text);
        expect(result).toEqual([
          'Bonjour',
          [{ name: 'test.pdf', url: 'https://example.com/test.pdf' }]
        ]);
      });
    });

    it('should round-trip a Dutch-localized html body regardless of the reader language', () => {
      let html = '';
      withLanguage('nl-NL', () => {
        html = MailHelper.attachDriveAttachmentsToHtmlBody('<h1>Hallo</h1>', attachments);
      });
      expect(html).toContain('---------- Drive-bijlagen ----------');

      withLanguage('fr-FR', () => {
        const [body, extracted] = MailHelper.extractDriveAttachmentsFromHtmlBody(html);
        expect(body).toBe('<h1>Hallo</h1>');
        expect(extracted).toHaveLength(1);
        expect(extracted[0].name).toBe('test.pdf');
      });
    });

    it('should expose every localized separator in ATTACHMENT_SEPARATORS for parsing', () => {
      // Guards against accidentally adding a localized separator without
      // appending it to ATTACHMENT_SEPARATORS, which would break parsing.
      expect(ATTACHMENT_SEPARATORS).toEqual(expect.arrayContaining([
        '---------- Drive attachments ----------',
        '---------- Fichiers joints ----------',
        '---------- Drive-bijlagen ----------',
      ]));
    });
  });

  // U+212A KELVIN SIGN: lowercases to ASCII "k". Written as an escape so a
  // literal degraded through a lossy encoding cannot quietly void the test.
  const KELVIN_SIGN = '\u212a';

  describe('asciiLower', () => {
    it('should lowercase ASCII A-Z', () => {
      expect(MailHelper.asciiLower('John.DOE-1_x')).toBe('john.doe-1_x');
    });

    it('should leave accented characters untouched', () => {
      expect(MailHelper.asciiLower('JOSE\u0301')).toBe('jose\u0301');
      expect(MailHelper.asciiLower('\u00c9')).toBe('\u00c9');
    });

    it('should never fold a Unicode look-alike onto ASCII', () => {
      // The whole point: toLowerCase() would turn this into "nick" and let it
      // collide with someone else's mailbox.
      expect(MailHelper.asciiLower(`nic${KELVIN_SIGN}`)).toBe(`nic${KELVIN_SIGN}`);
      expect(`nic${KELVIN_SIGN}`.toLowerCase()).toBe('nick');
    });
  });

  describe('splitEmail', () => {
    it('should split on the last @', () => {
      expect(MailHelper.splitEmail('"a@b"@example.com')).toEqual(['"a@b"', 'example.com']);
    });

    it.each(['', 'nodomain', '@example.com', 'user@'])(
      'should return undefined for %p',
      (value) => {
        expect(MailHelper.splitEmail(value)).toBeUndefined();
      }
    );
  });

  describe('normalizeEmailDomain', () => {
    it('should lowercase the domain', () => {
      expect(MailHelper.normalizeEmailDomain('user@EXAMPLE.COM')).toBe('user@example.com');
    });

    it('should keep the local part exactly as typed', () => {
      // RFC 5321 2.4: only the destination host may fold a local part.
      expect(MailHelper.normalizeEmailDomain('John.Doe@Example.com')).toBe('John.Doe@example.com');
    });

    it('should not fold a Unicode look-alike in the local part', () => {
      const input = `nic${KELVIN_SIGN}@Example.com`;
      expect(MailHelper.normalizeEmailDomain(input)).toBe(`nic${KELVIN_SIGN}@example.com`);
    });

    it('should lowercase an accented domain without transliterating it', () => {
      expect(MailHelper.normalizeEmailDomain('user@EXEMPLÉ.example')).toBe('user@exemplé.example');
    });

    it('should trim surrounding whitespace', () => {
      expect(MailHelper.normalizeEmailDomain('  user@Example.com  ')).toBe('user@example.com');
    });

    it('should leave a value with no domain untouched', () => {
      expect(MailHelper.normalizeEmailDomain('not-an-email')).toBe('not-an-email');
    });
  });

  describe('hasNonAsciiLocalPart / hasNonAsciiDomain', () => {
    it('should flag an accented local part only', () => {
      expect(MailHelper.hasNonAsciiLocalPart('josé@example.com')).toBe(true);
      expect(MailHelper.hasNonAsciiDomain('josé@example.com')).toBe(false);
    });

    it('should flag an accented domain only', () => {
      expect(MailHelper.hasNonAsciiDomain('user@exemplé.example')).toBe(true);
      expect(MailHelper.hasNonAsciiLocalPart('user@exemplé.example')).toBe(false);
    });

    it('should flag a Unicode look-alike in the local part', () => {
      expect(MailHelper.hasNonAsciiLocalPart(`nic${KELVIN_SIGN}@example.com`)).toBe(true);
    });

    it('should flag nothing for a plain ASCII address', () => {
      expect(MailHelper.hasNonAsciiLocalPart('John.Doe@Example.com')).toBe(false);
      expect(MailHelper.hasNonAsciiDomain('John.Doe@Example.com')).toBe(false);
    });

    it('should flag nothing for a malformed value', () => {
      expect(MailHelper.hasNonAsciiLocalPart('josé')).toBe(false);
      expect(MailHelper.hasNonAsciiDomain('josé')).toBe(false);
    });
  });

  describe('isValidEmail with accents', () => {
    it('should accept an accented domain, which the backend can send to', () => {
      expect(MailHelper.isValidEmail('user@exemplé.example')).toBe(true);
    });

    it('should accept an accented local part so it can be warned about', () => {
      // Rejecting it here would leave the user with an unexplained
      // unselectable value instead of the "not supported" warning.
      expect(MailHelper.isValidEmail('josé@example.com')).toBe(true);
    });

    it('should still reject values that are not addresses', () => {
      expect(MailHelper.isValidEmail('nodomain')).toBe(false);
      expect(MailHelper.isValidEmail('user@')).toBe(false);
      expect(MailHelper.isValidEmail('a b@example.com')).toBe(false);
    });

    // Widening to Unicode must not widen the domain checks. Zod's own
    // `unicodeEmail` (/^[^\s@"]{1,64}@[^\s@]{1,255}$/u) accepts every one
    // of these, which is why UNICODE_EMAIL_REGEX is hand-written.
    it.each([
      'test@com',
      'test@.com',
      'test@example.',
      '.test@example.com',
      'test@example..com',
      'text@example_23.com',
    ])('should keep rejecting the malformed domain %p', (email) => {
      expect(MailHelper.isValidEmail(email)).toBe(false);
    });

    it.each([
      'user@foo-.com',
      'user@\u0301foo.com',
      'user@-foo.com',
    ])('should reject the malformed domain label %p', (email) => {
      // A label may not end with a hyphen, nor start with a hyphen or a
      // combining mark (which has no base character to attach to there).
      expect(MailHelper.isValidEmail(email)).toBe(false);
    });

    it('should accept a punycode domain', () => {
      expect(MailHelper.isValidEmail('user@xn--exempl-gva.example')).toBe(true);
    });

    it('should accept an IDN top-level domain', () => {
      expect(MailHelper.isValidEmail('user@example.xn--p1ai')).toBe(true);
    });
  });
});
