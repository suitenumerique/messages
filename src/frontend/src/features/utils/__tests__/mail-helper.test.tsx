import MailHelper from '../mail-helper';

describe('MailHelper', () => {
  describe('markdownToHtml', () => {
    it('should convert markdown to HTML', async () => {
      const markdown = '**Hello World**';
      const html = await MailHelper.markdownToHtml(markdown);
      expect(html).toMatchInlineSnapshot(`
        "<div data-id="react-email-markdown"><p><strong style="font-weight:bold">Hello World</strong></p>
        </div>"
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
}); 