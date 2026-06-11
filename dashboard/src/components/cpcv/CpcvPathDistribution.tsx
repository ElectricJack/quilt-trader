interface Props {
  path_sharpes: number[];
  mean: number;
  ci: [number, number];
  width?: number;
  height?: number;
}

function StripPlot({ path_sharpes, mean, ci, width = 240, height = 140 }: Props) {
  const values = path_sharpes;
  const min = Math.min(0, ...values, ci[0]);
  const max = Math.max(0, ...values, ci[1]);
  const range = max - min || 1;
  const scaleX = (v: number) => ((v - min) / range) * (width - 20) + 10;
  return (
    <svg width={width} height={height}>
      <rect x={scaleX(ci[0])} y={20} width={scaleX(ci[1]) - scaleX(ci[0])} height={height - 40} fill="#6366f1" fillOpacity={0.15} />
      <line x1={scaleX(0)} y1={20} x2={scaleX(0)} y2={height - 20} stroke="#374151" strokeDasharray="2,2" />
      <line x1={scaleX(mean)} y1={20} x2={scaleX(mean)} y2={height - 20} stroke="#fff" strokeDasharray="4,2" />
      {values.map((v, i) => (
        <circle key={i} cx={scaleX(v)} cy={height / 2} r={4} fill="#10b981" fillOpacity={0.8} />
      ))}
    </svg>
  );
}

function Histogram({ path_sharpes, mean, ci, width = 240, height = 140 }: Props) {
  const min = Math.min(0, ...path_sharpes, ci[0]);
  const max = Math.max(0, ...path_sharpes, ci[1]);
  const range = max - min || 1;
  const nBins = Math.max(5, Math.ceil(Math.sqrt(path_sharpes.length)));
  const binSize = range / nBins;
  const counts = new Array(nBins).fill(0);
  for (const v of path_sharpes) {
    const i = Math.min(nBins - 1, Math.floor((v - min) / binSize));
    counts[i]++;
  }
  const maxCount = Math.max(...counts);
  const barW = (width - 20) / nBins;
  const scaleX = (v: number) => ((v - min) / range) * (width - 20) + 10;
  return (
    <svg width={width} height={height}>
      <rect x={scaleX(ci[0])} y={20} width={scaleX(ci[1]) - scaleX(ci[0])} height={height - 40} fill="#6366f1" fillOpacity={0.15} />
      {counts.map((c, i) => {
        const h = (c / maxCount) * (height - 40);
        return (
          <rect
            key={i}
            x={10 + i * barW}
            y={height - 20 - h}
            width={barW - 1}
            height={h}
            fill="#10b981"
            fillOpacity={0.7}
          />
        );
      })}
      <line x1={scaleX(0)} y1={20} x2={scaleX(0)} y2={height - 20} stroke="#374151" strokeDasharray="2,2" />
      <line x1={scaleX(mean)} y1={20} x2={scaleX(mean)} y2={height - 20} stroke="#fff" strokeDasharray="4,2" />
    </svg>
  );
}

export function CpcvPathDistribution(props: Props) {
  if (props.path_sharpes.length < 8) return <StripPlot {...props} />;
  return <Histogram {...props} />;
}
