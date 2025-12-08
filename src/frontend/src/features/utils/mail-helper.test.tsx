import MailHelper, { SUPPORTED_IMAP_DOMAINS, ATTACHMENT_SEPARATORS } from './mail-helper';
import DetectionMap from '@/features/i18n/attachments-detection-map.json';

describe('MailHelper', () => {
  describe('markdownToHtml', () => {
    it('should convert markdown to HTML', async () => {
      const markdown = '**Hello World**\n\n*Note: This is a test*';
      const html = await MailHelper.markdownToHtml(markdown);
      expect(html).toMatchInlineSnapshot(`
        "<p><strong style="font-weight:bold">Hello World</strong></p>
        <p><em style="font-style:italic">Note: This is a test</em></p>"
      `);
    });
  });

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
          "gmail.com",
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
        <ul>
        <li>
        <a class="drive-attachment" href="https://example.com/test.pdf" data-id="1" data-name="test.pdf" data-type="application/pdf" data-size="100" data-created_at="2021-01-01">test.pdf</a>
        </li>
        <li>
        <a class="drive-attachment" href="https://example.com/test.docx" data-id="2" data-name="test.docx" data-type="application/vnd.openxmlformats-officedocument.wordprocessingml.document" data-size="200" data-created_at="2021-01-02">test.docx</a>
        </li>
        </ul>

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
        <ul>
        <li>
        <a class="drive-attachment" href="https://example.com/test.pdf" data-id="1" data-name="test.pdf" data-type="application/pdf" data-size="100" data-created_at="2021-01-01">test.pdf</a>
        </li>
        </ul>

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
        <ul>
        <li>
        <a class="drive-attachment" href="https://example.com/test.pdf" data-id="1" data-name="test.pdf" data-type="application/pdf" data-size="100" data-created_at="2021-01-01">test.pdf</a>
        </li>
        </ul>

        "
      `);
    });

    it('should handle undefined attachments', () => {
      const htmlBody = '<h1>Hello, how are you today?</h1>';
      const result = MailHelper.attachDriveAttachmentsToHtmlBody(htmlBody, undefined);
      expect(result).toBe('<h1>Hello, how are you today?</h1>');
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

  describe('DetectionMap', () => {
    it('should not have invalid regex patterns', () => {
      // A test guard to ensure that the detection map does not contain malformed regex patterns
      const regexPatterns = MailHelper.getAttachmentKeywords(DetectionMap).filter((pattern) => pattern.startsWith('/') && pattern.endsWith('/'));
      for (const pattern of regexPatterns) {
        expect(() => new RegExp(pattern.slice(1, -1), 'i')).not.toThrowError();
      }
    });
  });
});
