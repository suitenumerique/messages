import clsx from "clsx";

type InfoRowProps = {
  label: string;
  rightContent: React.ReactNode | string;
};

export const InfoRow = ({ label, rightContent }: InfoRowProps) => {
  return (
    <div className="info-row">
      <div className="info-row__label">{label}</div>
      <div
        className={clsx("info-row__right-content", {
          "info-row__right-content__string": typeof rightContent === "string",
        })}
      >
        {rightContent}
      </div>
    </div>
  );
};
