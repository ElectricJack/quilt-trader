import { useAlgorithmGitStatus } from "../api/hooks";

export function GitStatusBadge({ algoId }: { algoId: string }) {
  const { data, isLoading, isError } = useAlgorithmGitStatus(algoId);
  if (isLoading) {
    return <span className="text-[10px] text-gray-500">checking…</span>;
  }
  if (isError || !data) {
    return <span className="text-[10px] text-gray-500">—</span>;
  }
  if (data.commits_behind === 0) {
    return (
      <span className="inline-block px-1.5 py-0.5 rounded text-[10px] bg-emerald-950 text-emerald-400">
        ✓ Up to date
      </span>
    );
  }
  if (data.commits_behind < 0) {
    return (
      <span
        className="inline-block px-1.5 py-0.5 rounded text-[10px] bg-gray-800 text-gray-400"
        title="Local commit not in default branch history"
      >
        diverged
      </span>
    );
  }
  return (
    <span
      className="inline-block px-1.5 py-0.5 rounded text-[10px] bg-yellow-950 text-yellow-400"
      title={`Default branch: ${data.default_branch}`}
    >
      ↓ {data.commits_behind} behind
    </span>
  );
}
