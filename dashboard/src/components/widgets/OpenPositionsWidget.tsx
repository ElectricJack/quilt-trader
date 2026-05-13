import { Widget } from "../Widget";
import { useAllInstances } from "../../api/hooks";

function summarizeState(state: Record<string, unknown>): string {
  const keys = Object.keys(state);
  if (keys.length === 0) return "No state keys";
  return keys.slice(0, 3).join(", ") + (keys.length > 3 ? `, +${keys.length - 3} more` : "");
}

export function OpenPositionsWidget() {
  const { data: instances, isLoading } = useAllInstances();

  const withState = (instances ?? []).filter(
    (inst) => inst.persisted_state && Object.keys(inst.persisted_state).length > 0
  );

  return (
    <Widget title="Open Positions" isLoading={isLoading}>
      {withState.length === 0 ? (
        <p className="text-gray-500 text-sm">No position data available</p>
      ) : (
        <ul className="space-y-2">
          {withState.map((inst) => (
            <li
              key={inst.id}
              className="py-1.5 border-b border-gray-800 last:border-b-0"
            >
              <div className="flex items-center gap-2 mb-0.5">
                <span className="text-xs text-gray-400 font-mono truncate max-w-[160px]">
                  {inst.id}
                </span>
              </div>
              <span className="text-xs text-gray-500">
                {summarizeState(inst.persisted_state!)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Widget>
  );
}
