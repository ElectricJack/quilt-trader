import { useState } from "react";
import { Plus, Pause, Play, Trash2, Pencil } from "lucide-react";
import { useDataGoals, useCreateGoal, useUpdateGoal, usePauseGoal, useResumeGoal, useDeleteGoal } from "../api/hooks";
import { CreateGoalModal } from "./CreateGoalModal";
import { ConfirmDialog } from "./ConfirmDialog";
import type { GoalCreate, DataGoal } from "../api/client";

function GoalCard({ goal, onPause, onResume, onDelete, onEdit }: {
  goal: DataGoal;
  onPause: () => void;
  onResume: () => void;
  onDelete: () => void;
  onEdit: () => void;
}) {
  const pct = goal.progress_pct;
  const phase = (goal as any).phase || "discovering";
  const discoveryProgress: string | null = (goal as any).discovery_progress;

  // Parse discovery progress for percentage and ETA
  let discoveryPct = 0;
  let discoveryEta = "";
  if (discoveryProgress) {
    const m = discoveryProgress.match(/^(\d+)\/(\d+)/);
    if (m) {
      const done = parseInt(m[1]);
      const total = parseInt(m[2]);
      discoveryPct = total > 0 ? (done / total) * 100 : 0;
      if (done > 0 && goal.created_at && goal.last_processed_at) {
        const started = new Date(goal.created_at).getTime();
        const now = new Date(goal.last_processed_at).getTime();
        const elapsed = (now - started) / 1000;
        const remaining = elapsed / done * (total - done);
        if (remaining < 60) discoveryEta = `~${Math.round(remaining)}s left`;
        else if (remaining < 3600) discoveryEta = `~${Math.round(remaining / 60)}m left`;
        else discoveryEta = `~${(remaining / 3600).toFixed(1)}h left`;
      }
    }
  }
  const statusColor = goal.status === "active" ? "text-green-400" : goal.status === "paused" ? "text-yellow-400" : "text-gray-400";
  const phaseLabel = phase === "discovering" ? "Discovering" : phase === "downloading" ? "Downloading" : "Complete";
  const phaseColor = phase === "discovering" ? "text-blue-400" : phase === "downloading" ? "text-amber-400" : "text-green-400";

  const freqs = (goal.config as any).frequencies || [(goal.config as any).frequency] || [];
  const configSummary = goal.goal_type === "options"
    ? `${(goal.config as any).underlying} ${Array.isArray(freqs) ? freqs.join("+") : freqs} options, ${(goal.config as any).strike_range} strikes`
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
          <button onClick={onEdit} className="p-1 hover:bg-gray-800 rounded" title="Edit">
            <Pencil size={14} className="text-gray-400" />
          </button>
          <button onClick={onDelete} className="p-1 hover:bg-gray-800 rounded" title="Delete">
            <Trash2 size={14} className="text-gray-400" />
          </button>
        </div>
      </div>
      <div>
        <div className="flex justify-between text-[10px] mb-1">
          <span className="text-gray-500">
            <span className={`font-medium ${phaseColor}`}>{phaseLabel}</span>
            {phase === "discovering" && discoveryProgress && ` — ${discoveryProgress}`}
            {phase === "discovering" && goal.total_items > 0 && ` (${goal.total_items.toLocaleString()} contracts found so far)`}
            {phase === "downloading" && ` — ${goal.completed_items.toLocaleString()} / ${goal.total_items.toLocaleString()} contracts`}
          </span>
          <span className="text-gray-500">
            {phase === "discovering" && discoveryPct > 0 && `${discoveryPct.toFixed(0)}%`}
            {phase === "downloading" && `${pct}%`}
            {discoveryEta && phase === "discovering" && ` · ${discoveryEta}`}
          </span>
        </div>
        <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
          <div className={`h-full rounded-full transition-all ${phase === "discovering" ? "bg-blue-500" : "bg-indigo-500"}`}
            style={{ width: phase === "discovering" ? `${Math.max(discoveryPct, 2)}%` : `${Math.min(pct, 100)}%` }} />
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
  const updateGoal = useUpdateGoal();
  const pauseGoal = usePauseGoal();
  const resumeGoal = useResumeGoal();
  const deleteGoal = useDeleteGoal();
  const [showCreate, setShowCreate] = useState(false);
  const [editingGoal, setEditingGoal] = useState<DataGoal | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const handleSubmit = (body: GoalCreate) => {
    if (editingGoal) {
      updateGoal.mutate({ id: editingGoal.id, body });
      setEditingGoal(null);
    } else {
      createGoal.mutate(body);
    }
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
              onDelete={() => setConfirmDelete(g.id)}
              onEdit={() => { setEditingGoal(g); setShowCreate(true); }} />
          ))}
        </div>
      )}
      <CreateGoalModal
        open={showCreate}
        onClose={() => { setShowCreate(false); setEditingGoal(null); }}
        onSubmit={handleSubmit}
        editGoal={editingGoal}
      />
      <ConfirmDialog
        open={confirmDelete !== null}
        title="Delete Goal"
        message="Are you sure you want to delete this goal? Downloads already completed won't be affected."
        onConfirm={() => { if (confirmDelete) deleteGoal.mutate(confirmDelete); setConfirmDelete(null); }}
        onCancel={() => setConfirmDelete(null)} />
    </div>
  );
}
