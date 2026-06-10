interface Props {
  points: number[];
  width?: number;
  height?: number;
}

export function EquitySparkline({ points, width = 140, height = 32 }: Props) {
  if (points.length === 0) {
    return <span className="text-xs text-gray-600">no data</span>;
  }
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const dx = points.length > 1 ? width / (points.length - 1) : 0;
  const d = points
    .map((p, i) => {
      const x = i * dx;
      const y = height - ((p - min) / range) * height;
      return `${i === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
  const stroke = points[points.length - 1] >= points[0] ? "#10b981" : "#ef4444";
  return (
    <svg width={width} height={height} className="overflow-visible">
      <path d={d} stroke={stroke} strokeWidth={1.5} fill="none" />
    </svg>
  );
}
