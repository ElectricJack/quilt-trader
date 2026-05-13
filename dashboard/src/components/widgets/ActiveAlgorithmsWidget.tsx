import { Widget } from "../Widget";
import { StatusBadge } from "../StatusBadge";
import { useAllInstances } from "../../api/hooks";

const MAX_VISIBLE = 5;

export function ActiveAlgorithmsWidget() {
  const { data: instances, isLoading } = useAllInstances();

  const running = (instances ?? []).filter((i) => i.status === "running");
  const visible = running.slice(0, MAX_VISIBLE);
  const overflow = running.length - MAX_VISIBLE;

  return (
    <Widget title="Active Algorithms" isLoading={isLoading}>
      <div className="text-4xl font-bold text-white mb-4">
        {running.length}
      </div>
      {running.length === 0 ? (
        <p className="text-gray-500 text-sm">No running instances</p>
      ) : (
        <ul className="space-y-2">
          {visible.map((inst) => (
            <li
              key={inst.id}
              className="flex items-center justify-between py-1 border-b border-gray-800 last:border-b-0"
            >
              <span className="text-xs text-gray-400 font-mono truncate max-w-[140px]">
                {inst.id}
              </span>
              <StatusBadge status={inst.status} />
            </li>
          ))}
          {overflow > 0 && (
            <li className="text-xs text-gray-500 pt-1">
              and {overflow} more…
            </li>
          )}
        </ul>
      )}
    </Widget>
  );
}
