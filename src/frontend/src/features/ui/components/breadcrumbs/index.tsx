import { Icon, IconSize, IconType } from "@gouvfr-lasuite/ui-components";
import { Button } from "@gouvfr-lasuite/ui-components";
import React, { ReactElement, ReactNode } from "react";
import { useTranslation } from "react-i18next";

export interface BreadcrumbsProps {
  items: { content: ReactNode }[];
  onBack?: () => void;
  displayBack?: boolean;
}

export const Breadcrumbs = ({
  items,
  onBack,
  displayBack = false,
}: BreadcrumbsProps) => {
  const { t } = useTranslation();

  return (
    <div className="c__breadcrumbs">
      {displayBack && (
        <Button
          icon={<Icon name="arrow_back" />}
          variant="secondary"
          onClick={onBack}
          disabled={items.length <= 1}
        >
          {t("Back")}
        </Button>
      )}

      {items.map((item, index) => {
        return (
          <React.Fragment key={index}>
            {index > 0 && (
              <Icon className="c__breadcrumbs__chevron" name="chevron_right" type={IconType.OUTLINED} size={IconSize.MEDIUM}/>
            )}
            {React.cloneElement(item.content as ReactElement<HTMLDivElement>, {
              className: `${
                (
                  (item.content as ReactElement<HTMLDivElement>).props as {
                    className?: string;
                  }
                ).className || ""
              } ${index === items.length - 1 ? "active" : ""}`,
            })}
          </React.Fragment>
        );
      })}
    </div>
  );
};
