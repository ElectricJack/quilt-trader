export interface DonutSegment {
  key: string;
  label: string;
  value_usd: number;
  percent: number;
  color: string;
}

interface DonutProps {
  segments: DonutSegment[];
  size?: number;
  thickness?: number;
}

export function Donut({ segments, size = 130, thickness = 26 }: DonutProps) {
  const radius = size / 2;
  const circumference = 2 * Math.PI * (radius - thickness / 2);

  let offset = 0;
  const arcs = segments.map((seg) => {
    const length = (seg.percent / 100) * circumference;
    const arc = (
      <circle
        key={seg.key}
        cx={radius}
        cy={radius}
        r={radius - thickness / 2}
        fill="none"
        stroke={seg.color}
        strokeWidth={thickness}
        strokeDasharray={`${length} ${circumference - length}`}
        strokeDashoffset={-offset}
        transform={`rotate(-90 ${radius} ${radius})`}
      />
    );
    offset += length;
    return arc;
  });

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      style={{ flexShrink: 0 }}
    >
      <circle
        cx={radius}
        cy={radius}
        r={radius - thickness / 2}
        fill="none"
        stroke="#1f2937"
        strokeWidth={thickness}
      />
      {arcs}
    </svg>
  );
}
