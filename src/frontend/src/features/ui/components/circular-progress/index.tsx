import { CSSProperties } from "react";
import clsx from "clsx";
import { Icon, IconType } from "@gouvfr-lasuite/ui-components";

interface CircularProgressProps {
  progress?: number;
  withLabel?: boolean;
  loading?: boolean;
}

export const CircularProgress = ({
  progress = 0,
  withLabel = false,
  loading = false,
}: CircularProgressProps) => {
  if (progress > 100) {
    progress = 100;
  }
  if (loading) {
    progress = 33;
  }

  // Fixed size of 24px for the component
  const fixedSize = 24;
  // Fixed size of 20px for the circle
  const circleSize = 20;

  // Calculate the radius based on the circle size
  const radius = circleSize / 2;
  const circumference = 2 * Math.PI * radius;

  // Calculate the dash offset based on progress
  const dashOffset = circumference - (progress / 100) * circumference;

  // Determine if we should show the check mark
  const isComplete = progress >= 100;

  return (
    <div
      className={clsx("circular-progress", { 'circular-progress--loading': loading })}
      style={{ '--var-size': `${fixedSize}px` } as CSSProperties}
    >
      {!isComplete && (
        <>
          {withLabel && !loading && <span className="circular-progress__label">{progress}</span>}
          <svg
            width={fixedSize}
            height={fixedSize}
            viewBox={`0 0 ${fixedSize} ${fixedSize}`}
            style={{ transform: isComplete ? "rotate(0deg)" : "rotate(-90deg)" }}
          >
            {/* Background circle - centered in the 24x24 container */}
            <circle
              className="circular-progress__background"
              cx={fixedSize / 2}
              cy={fixedSize / 2}
              r={radius}
            />

            {/* Progress circle - centered in the 24x24 container */}
            <circle
              className="circular-progress__foreground"
              cx={fixedSize / 2}
              cy={fixedSize / 2}
              r={radius}
              strokeDasharray={circumference}
              strokeDashoffset={dashOffset}
            />
          </svg>
        </>
      )}
      {/* Check mark when complete */}
      {isComplete && <Icon name="check_circle" type={IconType.FILLED} className="circular-progress__icon-completed" />}
    </div>
  );
};
