interface SparklineProps {
  points: number[];
  color?: string;
  height?: number;
  className?: string;
}

export function Sparkline({
  points,
  color = "#10b981",
  height = 26,
  className,
}: SparklineProps) {
  if (points.length < 2) {
    return (
      <svg
        viewBox="0 0 100 30"
        preserveAspectRatio="none"
        style={{ width: "100%", height, display: "block" }}
        className={className}
      />
    );
  }

  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;

  const path = points
    .map((p, i) => {
      const x = (i / (points.length - 1)) * 100;
      const y = 30 - ((p - min) / range) * 28 - 1;
      return `${i === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");

  return (
    <svg
      viewBox="0 0 100 30"
      preserveAspectRatio="none"
      style={{ width: "100%", height, display: "block" }}
      className={className}
    >
      <path
        d={path}
        stroke={color}
        strokeWidth={1.2}
        fill="none"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
