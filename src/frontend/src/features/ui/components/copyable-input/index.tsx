import React, { useState } from "react";
import { Button } from "@openfun/cunningham-react";
import { useTranslation } from "react-i18next";

export type CopyableInputProps = {
  value: string;
  readOnly?: boolean;
};

export function CopyableInput({ value, readOnly = true }: CopyableInputProps) {
  const { t } = useTranslation();
  const [showCopyButton, setShowCopyButton] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy text: ', err);
    }
  };

  const handleFocus = (event: React.FocusEvent<HTMLInputElement>) => {
    setTimeout(() => event.target.select(), 100);
  };

  return (
    <div 
      className="copyable-input"
      onMouseEnter={() => setShowCopyButton(true)}
      onMouseLeave={() => setShowCopyButton(false)}
    >
      <input
        type="text"
        value={value}
        readOnly={readOnly}
        onFocus={handleFocus}
        className="copyable-input__input"
      />
      {showCopyButton && (
        <Button
          size="small"
          color="secondary"
          onClick={handleCopy}
          style={{
            minWidth: 'auto',
            padding: '4px 8px',
            fontSize: '0.75rem'
          }}
        >
          {copied ? '✓' : t("admin_maildomains_dns.copy")}
        </Button>
      )}
    </div>
  );
} 