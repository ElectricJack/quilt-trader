import { useState } from "react";
import { Plus, Pause, Play, Trash2 } from "lucide-react";
import { useDataGoals, useCreateGoal, usePauseGoal, useResumeGoal, useDeleteGoal } from "../api/hooks";
import { CreateGoalModal } from "./CreateGoalModal";
import { ConfirmDialog } from "./ConfirmDialog";
import type { GoalCreate, DataGoal } from "../api/client";

function GoalCard({ goal, onPause, onResume, onDelete }: {
  goal: DataGoal;
  onPause: () => void;
  onResume: () => void;
  onDelete: () => void;
}) {
  const pct = goal.progress_pct;
  const statusColor = goal.status === "active" ? "text-green-400" : goal.status === "paused" ? "text-yellow-400" : "text-gray-400";

  const configSummary = goal.goal_type === "options"
    ? `${(goal.config as any).underlying} ${(goal.config as any).frequency} options, ${(goal.config as any).strike_range} strikes`
    : `${((goal.config as any).symbols || []).join(", ")} — ${((goal.config as any).timeframes || []).join(", ")}`;

  return (
    <div className="bg-gray-900 rounded-lg p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-gray-100">{goal.name}</h3>
          <p className="text-xs text-gray-500 mt-0.5">{configSummary}</p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-xs font-medium uppercase ${statusColor}`}>{goal.status}</span>
          {goal.status === "active" && (
            <button onClick={onPause} className="p-1 hover:bg-gray-800 rounded" title="Pause">
              <Pause size={14} className="text-gray-400" />
            </button>
          )}
          {goal.status === "paused" && (
            <button onClick={onResume} className="p-1 hover:bg-gray-800 rounded" title="Resume">
              <Play size={14} className="text-gray-400" />
            </button>
          )}
          <button onClick={onDelete} className="p-1 hover:bg-gray-800 rounded" title="Delete">
            <Trash2 size={14} className="text-gray-400" />
          </button>
        </div>
      </div>
      <div>
        <div className="flex justify-between text-[10px] text-gray-500 mb-1">
          <span>{goal.completed_items.toLocaleString()} / {goal.total_items.toLocaleString()} items</span>
          <span>{pct}%</span>
        </div>
        <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
          <div className="h-full bg-indigo-500 rounded-full transition-all" style={{ width: `${Math.min(pct, 100)}%` }} />
        </div>
      </div>
      <div className="flex gap-4 text-[10px] text-gray-500">
        <span>{(goal.config as any).date_start} → {(goal.config as any).date_end}</span>
        <span>{(goal.config as any).provider}</span>
        {goal.last_processed_at && <span>Last checked: {new Date(goal.last_processed_at).toLocaleTimeString()}</span>}
      </div>
      {goal.error_message && (
        <p className="text-xs text-red-400">{goal.error_message}</p>
      )}
    </div>
  );
}

export function DataGoalsTab() {
  const { data: goals, isLoading } = useDataGoals();
  const createGoal = useCreateGoal();
  const pauseGoal = usePauseGoal();
  const resumeGoal = useResumeGoal();
  const deleteGoal = useDeleteGoal();
  const [showCreate, setShowCreate] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const handleCreate = (body: GoalCreate) => {
    createGoal.mutate(body);
  };

  if (isLoading) return <div className="text-gray-500 text-sm py-4">Loading goals...</div>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-300">Data Goals</h2>
        <button onClick={() => setShowCreate(true)} className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white text-xs rounded">
          <Plus size={12} /> New Goal
        </button>
      </div>
      {(!goals || goals.length === 0) ? (
        <div className="text-gray-500 text-sm py-8 text-center">
          No data goals yet. Create one to start downloading data automatically.
        </div>
      ) : (
        <div className="space-y-2">
          {goals.map((g) => (
            <GoalCard key={g.id} goal={g}
              onPause={() => pauseGoal.mutate(g.id)}
              onResume={() => resumeGoal.mutate(g.id)}
              onDelete={() => setConfirmDelete(g.id)} />
          ))}
        </div>
      )}
      <CreateGoalModal open={showCreate} onClose={() => setShowCreate(false)} onSubmit={handleCreate} />
      <ConfirmDialog
        open={confirmDelete !== null}
        title="Delete Goal"
        message="Are you sure you want to delete this goal? Downloads already completed won't be affected."
        onConfirm={() => { if (confirmDelete) deleteGoal.mutate(confirmDelete); setConfirmDelete(null); }}
        onCancel={() => setConfirmDelete(null)} />
    </div>
  );
}
