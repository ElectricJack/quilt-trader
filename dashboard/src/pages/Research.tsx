import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Microscope, Plus } from "lucide-react";
import { useResearchSessions } from "../hooks/useResearchSessions";
import { NewSessionModal } from "../components/NewSessionModal";

export function Research() {
  const [modalOpen, setModalOpen] = useState(false);
  const nav = useNavigate();
  const q = useResearchSessions();

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white flex items-center gap-2">
          <Microscope size={22} /> Research
        </h1>
        <button
          onClick={() => setModalOpen(true)}
          className="bg-indigo-600 hover:bg-indigo-500 text-white text-sm px-3 py-1.5 rounded flex items-center gap-1"
        >
          <Plus size={14} /> New Session
        </button>
      </div>

      {q.isLoading && <div className="text-gray-400 text-sm">Loading…</div>}
      {q.error && <div className="text-red-400 text-sm">Failed to load sessions</div>}

      {q.data && q.data.length === 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-10 text-center">
          <Microscope size={32} className="mx-auto text-gray-600 mb-3" />
          <p className="text-gray-400 mb-4">No research sessions yet.</p>
          <button
            onClick={() => setModalOpen(true)}
            className="bg-indigo-600 hover:bg-indigo-500 text-white text-sm px-4 py-2 rounded"
          >
            Create your first session
          </button>
        </div>
      )}

      {q.data && q.data.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-950 text-gray-400 text-xs">
              <tr>
                <th className="px-4 py-2 text-left">Name</th>
                <th className="px-4 py-2 text-left">Algorithm</th>
                <th className="px-4 py-2 text-left">Date range</th>
                <th className="px-4 py-2 text-left">Status</th>
                <th className="px-4 py-2 text-left">Hypothesis</th>
                <th className="px-4 py-2 text-right">Runs</th>
                <th className="px-4 py-2 text-left">Created</th>
              </tr>
            </thead>
            <tbody>
              {q.data.map((s) => (
                <tr
                  key={s.id}
                  onClick={() => nav(`/research/sessions/${s.id}`)}
                  className="border-t border-gray-800 cursor-pointer hover:bg-gray-800/50 text-gray-200"
                >
                  <td className="px-4 py-2 font-medium">{s.name}</td>
                  <td className="px-4 py-2 text-gray-400 font-mono text-xs">{s.algorithm_id}</td>
                  <td className="px-4 py-2 text-gray-400 font-mono text-xs whitespace-nowrap">
                    {s.date_range_start} → {s.date_range_end}
                  </td>
                  <td className="px-4 py-2">{s.status}</td>
                  <td className="px-4 py-2 text-gray-400 truncate max-w-md" title={s.hypothesis}>
                    {s.hypothesis.length > 80 ? s.hypothesis.slice(0, 80) + "…" : s.hypothesis}
                  </td>
                  <td className="px-4 py-2 text-right">{s.n_runs}</td>
                  <td className="px-4 py-2 text-gray-500">{s.created_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <NewSessionModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onCreated={(id) => { setModalOpen(false); nav(`/research/sessions/${id}`); }}
      />
    </div>
  );
}
