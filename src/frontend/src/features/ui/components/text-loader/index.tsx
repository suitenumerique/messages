interface TextLoaderProps {
  lines?: number;
}

export const TextLoader = ({ lines = 3 }: TextLoaderProps) => {
  return (
    <div className="text-loader">
      {Array.from({ length: lines }).map((_, index) => (
        <div
          key={index}
          className={`text-loader__line ${
            index === lines - 1 ? "text-loader__line--short" : ""
          }`}
        />
      ))}
    </div>
  );
};

