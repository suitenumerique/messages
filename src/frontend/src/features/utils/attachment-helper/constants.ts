const ICON_PATHS = {
    mimeArchive: "/images/files/icons/mime-archive.svg",
    mimeArchiveMini: "/images/files/icons/mime-archive-mini.svg",
    mimeAudio: "/images/files/icons/mime-audio.svg",
    mimeAudioMini: "/images/files/icons/mime-audio-mini.svg",
    mimeCalc: "/images/files/icons/mime-calc.svg",
    mimeCalcMini: "/images/files/icons/mime-calc-mini.svg",
    mimeDoc: "/images/files/icons/mime-doc.svg",
    mimeDocMini: "/images/files/icons/mime-doc-mini.svg",
    mimeImage: "/images/files/icons/mime-image.svg",
    mimeImageMini: "/images/files/icons/mime-image-mini.svg",
    mimeOther: "/images/files/icons/mime-other.svg",
    mimePdf: "/images/files/icons/mime-pdf.svg",
    mimePdfMini: "/images/files/icons/mime-pdf-mini.svg",
    mimePowerpoint: "/images/files/icons/mime-powerpoint.svg",
    mimePowerpointMini: "/images/files/icons/mime-powerpoint-mini.svg",
    mimeVideo: "/images/files/icons/mime-video.svg",
    mimeVideoMini: "/images/files/icons/mime-video-mini.svg",
}

export enum MimeCategory {
  CALC = "calc",
  DOC = "doc",
  IMAGE = "image",
  OTHER = "other",
  PDF = "pdf",
  POWERPOINT = "powerpoint",
  AUDIO = "audio",
  VIDEO = "video",
  ARCHIVE = "archive",
}

export const MIME_TO_ICON = {
  [MimeCategory.CALC]: ICON_PATHS.mimeCalc,
  [MimeCategory.DOC]: ICON_PATHS.mimeDoc,
  [MimeCategory.IMAGE]: ICON_PATHS.mimeImage,
  [MimeCategory.OTHER]: ICON_PATHS.mimeOther,
  [MimeCategory.PDF]: ICON_PATHS.mimePdf,
  [MimeCategory.POWERPOINT]: ICON_PATHS.mimePowerpoint,
  [MimeCategory.AUDIO]: ICON_PATHS.mimeAudio,
  [MimeCategory.VIDEO]: ICON_PATHS.mimeVideo,
  [MimeCategory.ARCHIVE]: ICON_PATHS.mimeArchive,

};

export const MIME_TO_ICON_MINI = {
  [MimeCategory.CALC]: ICON_PATHS.mimeCalcMini,
  [MimeCategory.DOC]: ICON_PATHS.mimeDocMini,
  [MimeCategory.IMAGE]: ICON_PATHS.mimeImageMini,
  [MimeCategory.OTHER]: ICON_PATHS.mimeOther,
  [MimeCategory.PDF]: ICON_PATHS.mimePdfMini,
  [MimeCategory.POWERPOINT]: ICON_PATHS.mimePowerpointMini,
  [MimeCategory.AUDIO]: ICON_PATHS.mimeAudioMini,
  [MimeCategory.VIDEO]: ICON_PATHS.mimeVideoMini,
  [MimeCategory.ARCHIVE]: ICON_PATHS.mimeArchiveMini,
};

export const ICONS = {
	"mini": MIME_TO_ICON_MINI,
	"normal": MIME_TO_ICON
}


export const MIME_TO_FORMAT_TRANSLATION_KEY = {
  [MimeCategory.CALC]: "mime.calc",
  [MimeCategory.DOC]: "mime.doc",
  [MimeCategory.IMAGE]: "mime.image",
  [MimeCategory.OTHER]: "mime.other",
  [MimeCategory.PDF]: "mime.pdf",
  [MimeCategory.POWERPOINT]: "mime.powerpoint",
  [MimeCategory.AUDIO]: "mime.audio",
  [MimeCategory.VIDEO]: "mime.video",
  [MimeCategory.ARCHIVE]: "mime.archive",
};


export const MIME_MAP = {
    [MimeCategory.CALC]: [
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      "text/csv",
    ],
    [MimeCategory.PDF]: [
      "application/pdf"
    ],
    [MimeCategory.DOC]: [
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ],
    [MimeCategory.POWERPOINT]: [
      "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ],
    [MimeCategory.ARCHIVE]: [
      "application/zip",
      "application/x-7z-compressed",
      "application/x-rar-compressed",
      "application/x-tar",
      "application/x-rar",
      "application/octet-stream",
    ],
};

// This is used to map mimetypes to categories to get a O(1) lookup
export const MIME_TO_CATEGORY = Object.entries(MIME_MAP).reduce((acc, [category, mimes]) => {
    mimes.forEach((mime) => {
      acc[mime] = category as MimeCategory;
    });
    return acc;
  }, {} as Record<string, MimeCategory>);
    
export const CALC_EXTENSIONS = ["numbers", "xlsx", "xls"];
