import React, { useState } from "react";
import { Button } from "@gouvfr-lasuite/ui-components";
import { useTranslation } from "react-i18next";
import { handle } from "@/features/utils/errors";
import { Icon } from "@gouvfr-lasuite/ui-components";

export type CopyableInputProps = Omit<React.DetailedHTMLProps<React.InputHTMLAttributes<HTMLInputElement>, HTMLInputElement>, 'value'> & {
  value: string | number;
};

export function CopyableInput({ value, readOnly = true, ...props }: CopyableInputProps) {
  const { t } = useTranslation();
  const [showCopyButton, setShowCopyButton] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value.toString());
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (error) {
      handle(new Error('Failed to copy text.'), { extra: { error } });
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
        readOnly={readOnly}
        {...props}
        value={value}
        onFocus={handleFocus}
        className="copyable-input__input"
      />
      {showCopyButton && (
        <Button
          size="nano"
          variant="secondary"
          onClick={handleCopy}
          aria-label={copied ? t("Copied") : t("Copy")}
          aria-live="polite"
        >
          {copied ? <Icon name="check" /> : t("Copy")}
        </Button>
      )}
    </div>
  );
}
