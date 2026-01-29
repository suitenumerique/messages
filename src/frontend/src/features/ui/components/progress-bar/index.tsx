type ProgressBarProps = {
    progress: number | null;
    indeterminate?: boolean;
}

const ProgressBar = ({ progress, indeterminate = false }: ProgressBarProps) => {
    const isIndeterminate = indeterminate || progress === null;

    return (
        <div className={`progress-bar ${isIndeterminate ? 'progress-bar--indeterminate' : ''}`}>
            <div
                className="progress-bar__progress"
                style={isIndeterminate ? undefined : { width: `${progress}%` }}
            />
        </div>
    )
}

export default ProgressBar;
