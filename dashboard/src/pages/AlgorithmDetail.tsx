import { useParams } from "react-router-dom";

export function AlgorithmDetail() {
  const { id } = useParams<{ id: string }>();
  return (
    <div>
      <h1 className="text-2xl font-bold text-white">Algorithm Detail</h1>
      <p className="text-gray-400 mt-2 text-sm">ID: {id}</p>
    </div>
  );
}
