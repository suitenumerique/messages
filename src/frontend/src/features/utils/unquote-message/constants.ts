/**
 * Patterns for detecting forwarded messages in different languages
 * These indicate the content is a forward rather than a reply
 */
export const FORWARD_PATTERNS = [
  // English
  /^>?-*\s*forwarded\s+message/i,
  /^>?\s*begin\s+forwarded\s+message/i,
  /^>?\s*fwd:/i,
  // French
  /^>?\s*début\s+du\s+message\s+réexpédié/i,
  /^>?-*\s*message\s+transféré/i,
  /^>?\s*tr:/i,
  // German
  /^>?-*\s*weitergeleitete\s+nachricht/i,
  /^>?\s*wg:/i,
  // Spanish
  /^>?-*\s*mensaje\s+reenviado/i,
  /^>?\s*rv:/i,
  // Italian
  /^>?-*\s*messaggio\s+inoltrato/i,
  // Portuguese
  /^>?-*\s*mensagem\s+encaminhada/i,
  // Dutch
  /^>?-*\s*doorgestuurd\s+bericht/i,
  // Polish
  /^>?-*\s*wiadomość\s+przekazana/i,
  // Russian
  /^>?-*\s*пересланное\s+сообщение/i,
  // Japanese
  /^>?-*\s*転送されたメッセージ/i,
  // Chinese
  /^>?-*\s*转发的邮件/i,
];

/**
 * Standard reply patterns in different languages
 * These patterns match common email reply headers
 * Ported from Python unquotemail library
 */
export const REPLY_PATTERNS = [

  // ==================== Main Language Patterns ====================
  // English - On DATE, NAME <EMAIL> wrote:
  /^>*-*[^\S\r\n]{0,20}((on|in a message dated)\s.{1,500}\s.{1,500}?(wrote|sent)\s*:)\s?-*/im,
  // French - Le DATE, NAME a écrit:
  /^>*-*[^\S\r\n]{0,20}((le)\s.{1,500}\s.{1,500}?(écrit)\s*:)\s?/im,
  // Spanish - El DATE, NAME escribió:
  /^>*-*[^\S\r\n]{0,20}((el)\s.{1,500}\s.{1,500}?(escribió)\s*:)\s?/im,
  // Italian - Il DATE, NAME scritto:
  /^>*-*[^\S\r\n]{0,20}((il)\s.{1,500}\s.{1,500}?(scritto)\s*:)\s?/im,
  // Portuguese - Em DATE, NAME escreveu:
  /^>*-*[^\S\r\n]{0,20}((em)\s.{1,500}\s.{1,500}?(escreveu)\s*:)\s?/im,
  // German - Am DATE schrieb NAME <EMAIL>:
  /^[^\S\r\n]{0,20}(am\s.{1,500}\s)schrieb.{1,500}\s?(\[|<).{1,500}(\]|>):/im,
  // Dutch - Op DATE, schreef NAME <EMAIL>:
  /^[^\S\r\n]{0,20}(op\s[\s\S]{1,500}?(schreef|verzond|geschreven)[^\r\n]+:)/im,
  // Polish - W dniu DATE, NAME pisze|napisał:
  /^[^\S\r\n]{0,20}((w\sdniu|dnia)\s[\s\S]{1,500}?(pisze|napisał(\(a\))?):)/im,
  // Swedish/Danish - Den DATE skrev NAME <EMAIL>:
  // `[^\S\r\n]` (horizontal whitespace) instead of `\s`: with the m flag
  // an unbounded `\s` quantifier crosses newlines and backtracks once
  // per line start — quadratic in the number of lines of the body.
  /^[^\S\r\n]{0,20}(den|d.)?\s?.{1,500}\s?skrev\s?".{1,500}"[^\S\r\n]{0,20}[\[|<].{1,500}[\]|>]\s?:/im,
  // Vietnamese - Vào DATE đã viết NAME <EMAIL>:
  /^[^\S\r\n]{0,20}(vào\s.{1,500}\sđã viết\s.{1,500}:)/im,
  // Finnish - pe DATE NAME <EMAIL> kirjoitti:
  /^[^\S\r\n]{0,20}(pe\s.{1,500}\s.{1,500}kirjoitti:)/im,
  // Chinese - 在 DATE, TIME, NAME 写道：
  /^(在[\s\S]{1,500}写道：)/m,

  // ==================== Outlook 2019 Patterns ====================
  // Outlook 2019 (Norwegian) — horizontal whitespace only, see the
  // Swedish/Danish pattern above.
  /^\s?.{1,500}[^\S\r\n]{0,20}[\[|<].{1,500}[\]|>]\s?skrev følgende den\s?.{1,500}\s?:/m,
  // Outlook 2019 (Czech)
  /^\s?dne\s?.{1,500}\,\s?.{1,500}\s*[\[|<].{1,500}[\]|>]\s?napsal\(a\)\s?:/im,
  // Outlook 2019 (Russian)
  /^\s?.{1,500}\s?пользователь\s?".{1,500}"\s*[\[|<].{1,500}[\]|>]\s?написал\s?:/im,
  // Outlook 2019 (Slovak)
  /^\s?.{1,500}\s?používateľ\s?.{1,500}\s*\([\[|<].{1,500}[\]|>]\)\s?napísal\s?:/im,
  // Outlook 2019 (Swedish)
  /\s?Den\s?.{1,500}\s?skrev\s?".{1,500}"\s*[\[|<].{1,500}[\]|>]\s?följande\s?:/m,
  // Outlook 2019 (Turkish)
  /^\s?".{1,500}"\s*[\[|<].{1,500}[\]|>]\,\s?.{1,500}\s?tarihinde şunu yazdı\s?:/im,
  // Outlook 2019 (Hungarian)
  /^\s?.{1,500}\s?időpontban\s?.{1,500}\s*[\[|<|(].{1,500}[\]|>|)]\s?ezt írta\s?:/im,

  // ==================== Additional Patterns ====================
  // NAME <EMAIL> schrieb:
  /^(.{1,500}\s<.{1,500}>\sschrieb\s?:)/im,
  // NAME on DATE wrote:
  /^(.{1,500}\son.{0,500}at.{0,500}wrote:)/im,
  // "From: NAME <EMAIL>" (multiple languages)
  /^[^\S\r\n]{0,20}((from|van|de|von|da)\s?:.{1,500}\s?\n?[^\S\r\n]{0,20}(\[|<).{1,500}(\]|>))/im,

  // ==================== Date Starting Patterns ====================
  // Korean - DATE TIME NAME 작성:
  /^(20[0-9]{2}\..{1,500}\s작성:)$/m,
  // Japanese - DATE TIME、NAME のメッセージ:
  /^(20[0-9]{2}\/.{1,500}のメッセージ:)/m,
  // ISO Date format - 20YY-MM-DD HH:II GMT+01:00 NAME <EMAIL>:
  /^(20[0-9]{2})-([0-9]{2}).([0-9]{2}).([0-9]{2}):([0-9]{2})\n?(.{0,500})>:/m,
  // European Date format - DD.MM.20YY HH:II NAME <EMAIL>
  /^([0-9]{2}).([0-9]{2}).(20[0-9]{2})(.{0,500})(([0-9]{2}).([0-9]{2}))(.{0,500})"\s*<(.{0,500})>\s*:/m,
  // Time first format - HH:II, DATE, NAME <EMAIL>:
  /^[0-9]{2}:[0-9]{2}(.{0,500})[0-9]{4}(.{0,500})"\s*<(.{0,500})>\s*:/m,
  // Russian format - 02.04.2012 14:20 пользователь "bob@example.com" <bob@xxx.mailgun.org> написал:
  /(\d+\/\d+\/\d+|\d+\.\d+\.\d+)[^\r\n]{0,500}\s\S+@\S+:/,
  // ISO 8601 with timezone - 2014-10-17 11:28 GMT+03:00 Bob <bob@example.com>:
  /\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s+GMT[^\r\n]{0,500}\s\S+@\S+:/i,
  // RFC 2822 format - Thu, 26 Jun 2014 14:00:51 +0400 Bob <bob@example.com>:
  /\S{3,10},\s+\d\d?\s+\S{3,10}\s+20\d\d,?\s+\d\d?:\d\d(:\d\d)?[^\r\n]{0,200}@\S+:/,

  // ==================== Dash Delimiter Patterns ====================
  // Original Message delimiter (multi-language)
  new RegExp(
    `^>?[^\\S\\r\\n]{0,20}-{3,12}\\s*(` +
      `original message|` +
      `reply message|` +
      `original text|` +
      `message d'origine|` +
      `original email|` +
      `ursprüngliche nachricht|` +
      `original meddelelse|` +
      `original besked|` +
      `original meddelande|` +
      `originalbericht|` +
      `originalt meddelande|` +
      `originalt melding|` +
      `alkuperäinen viesti|` +
      `originalna poruka|` +
      `originalna správa|` +
      `originálna správa|` +
      `originální zpráva|` +
      `původní zpráva|` +
      `antwort nachricht|` +
      `oprindelig besked|` +
      `oprindelig meddelelse` +
      `)\\s*-{3,12}\\s*`,
    "im"
  ),
  // Generic separators
  /\r?\n[^\S\r\n]{0,20}_{5,}\s*\r?\n/,
  /\r?\n[^\S\r\n]{0,20}-{5,}\s*\r?\n/,
  // Quote markers with ">" at line start
  /\r?\n[^\S\r\n]{0,20}>+\s*.{1,500}\r?\n/,
  // Legacy patterns for backward compatibility
  /\r?\n[^\S\r\n]{0,20}From:\s+.{1,500}?\r?\n\s*Sent:\s+.{1,500}?\r?\n\s*To:\s+.{1,500}?\r?\n\s*Subject:\s+.{1,500}?\r?\n/i,
];
