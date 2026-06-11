import { useParams } from "react-router-dom";
import { Backtests } from "./Backtests";

export function AlgorithmBacktestsList() {
  const { id } = useParams<{ id: string }>();
  return <Backtests algorithmId={id} />;
}
