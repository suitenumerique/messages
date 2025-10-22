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
  /^>*-*\s*((on|in a message dated)\s.+\s.+?(wrote|sent)\s*:)\s?-*/im,
  // French - Le DATE, NAME a écrit:
  /^>*-*\s*((le)\s.+\s.+?(écrit)\s*:)\s?/im,
  // Spanish - El DATE, NAME escribió:
  /^>*-*\s*((el)\s.+\s.+?(escribió)\s*:)\s?/im,
  // Italian - Il DATE, NAME scritto:
  /^>*-*\s*((il)\s.+\s.+?(scritto)\s*:)\s?/im,
  // Portuguese - Em DATE, NAME escreveu:
  /^>*-*\s*((em)\s.+\s.+?(escreveu)\s*:)\s?/im,
  // German - Am DATE schrieb NAME <EMAIL>:
  /^\s*(am\s.+\s)schrieb.+\s?(\[|<).+(\]|>):/im,
  // Dutch - Op DATE, schreef NAME <EMAIL>:
  /^\s*(op\s[\s\S]+?(schreef|verzond|geschreven)[\s\S]+:)/im,
  // Polish - W dniu DATE, NAME pisze|napisał:
  /^\s*((w\sdniu|dnia)\s[\s\S]+?(pisze|napisał(\(a\))?):)/im,
  // Swedish/Danish - Den DATE skrev NAME <EMAIL>:
  /^\s*(den|d.)?\s?.+\s?skrev\s?".+"\s*[\[|<].+[\]|>]\s?:/im,
  // Vietnamese - Vào DATE đã viết NAME <EMAIL>:
  /^\s*(vào\s.+\sđã viết\s.+:)/im,
  // Finnish - pe DATE NAME <EMAIL> kirjoitti:
  /^\s*(pe\s.+\s.+kirjoitti:)/im,
  // Chinese - 在 DATE, TIME, NAME 写道：
  /^(在[\s\S]+写道：)/m,

  // ==================== Outlook 2019 Patterns ====================
  // Outlook 2019 (Norwegian)
  /^\s?.+\s*[\[|<].+[\]|>]\s?skrev følgende den\s?.+\s?:/m,
  // Outlook 2019 (Czech)
  /^\s?dne\s?.+\,\s?.+\s*[\[|<].+[\]|>]\s?napsal\(a\)\s?:/im,
  // Outlook 2019 (Russian)
  /^\s?.+\s?пользователь\s?".+"\s*[\[|<].+[\]|>]\s?написал\s?:/im,
  // Outlook 2019 (Slovak)
  /^\s?.+\s?používateľ\s?.+\s*\([\[|<].+[\]|>]\)\s?napísal\s?:/im,
  // Outlook 2019 (Swedish)
  /\s?Den\s?.+\s?skrev\s?".+"\s*[\[|<].+[\]|>]\s?följande\s?:/m,
  // Outlook 2019 (Turkish)
  /^\s?".+"\s*[\[|<].+[\]|>]\,\s?.+\s?tarihinde şunu yazdı\s?:/im,
  // Outlook 2019 (Hungarian)
  /^\s?.+\s?időpontban\s?.+\s*[\[|<|(].+[\]|>|)]\s?ezt írta\s?:/im,

  // ==================== Additional Patterns ====================
  // NAME <EMAIL> schrieb:
  /^(.+\s<.+>\sschrieb\s?:)/im,
  // NAME on DATE wrote:
  /^(.+\son.*at.*wrote:)/im,
  // "From: NAME <EMAIL>" (multiple languages)
  /^\s*((from|van|de|von|da)\s?:.+\s?\n?\s*(\[|<).+(\]|>))/im,

  // ==================== Date Starting Patterns ====================
  // Korean - DATE TIME NAME 작성:
  /^(20[0-9]{2}\..+\s작성:)$/m,
  // Japanese - DATE TIME、NAME のメッセージ:
  /^(20[0-9]{2}\/.+のメッセージ:)/m,
  // ISO Date format - 20YY-MM-DD HH:II GMT+01:00 NAME <EMAIL>:
  /^(20[0-9]{2})-([0-9]{2}).([0-9]{2}).([0-9]{2}):([0-9]{2})\n?(.*)>:/m,
  // European Date format - DD.MM.20YY HH:II NAME <EMAIL>
  /^([0-9]{2}).([0-9]{2}).(20[0-9]{2})(.*)(([0-9]{2}).([0-9]{2}))(.*)"\s*<(.*)>\s*:/m,
  // Time first format - HH:II, DATE, NAME <EMAIL>:
  /^[0-9]{2}:[0-9]{2}(.*)[0-9]{4}(.*)"\s*<(.*)>\s*:/m,
  // Russian format - 02.04.2012 14:20 пользователь "bob@example.com" <bob@xxx.mailgun.org> написал:
  /(\d+\/\d+\/\d+|\d+\.\d+\.\d+)[\s\S]*\s\S+@\S+:/,
  // ISO 8601 with timezone - 2014-10-17 11:28 GMT+03:00 Bob <bob@example.com>:
  /\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s+GMT[\s\S]*\s\S+@\S+:/i,
  // RFC 2822 format - Thu, 26 Jun 2014 14:00:51 +0400 Bob <bob@example.com>:
  /\S{3,10},\s+\d\d?\s+\S{3,10}\s+20\d\d,?\s+\d\d?:\d\d(:\d\d)?(\s+\S+){3,6}@\S+:/,

  // ==================== Dash Delimiter Patterns ====================
  // Original Message delimiter (multi-language)
  new RegExp(
    `^>?\\s*-{3,12}\\s*(` +
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
  /\r?\n\s*_{5,}\s*\r?\n/,
  /\r?\n\s*-{5,}\s*\r?\n/,
  // Quote markers with ">" at line start
  /\r?\n\s*>+\s*.+\r?\n/,
  // Legacy patterns for backward compatibility
  /\r?\n\s*From:\s+.+?\r?\n\s*Sent:\s+.+?\r?\n\s*To:\s+.+?\r?\n\s*Subject:\s+.+?\r?\n/i,
];
