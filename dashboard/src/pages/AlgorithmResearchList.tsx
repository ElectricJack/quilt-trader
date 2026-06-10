import { useParams } from "react-router-dom";
import { Research } from "./Research";

export function AlgorithmResearchList() {
  const { id } = useParams<{ id: string }>();
  return <Research algorithmId={id} />;
}
