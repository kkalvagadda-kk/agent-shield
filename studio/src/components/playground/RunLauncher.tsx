import { Loader2, Play } from "lucide-react";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { launchDurableRun } from "../../api/playgroundApi";

interface RunLauncherProps {
  agentName: string;
  versionId?: string;
  onRunStarted: (runId: string) => void;
}

export default function RunLauncher({ agentName, versionId, onRunStarted }: RunLauncherProps) {
  const [payload, setPayload] = useState('{\n  "message": "Hello"\n}');
  const [parseError, setParseError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: (inputPayload: Record<string, unknown>) =>
      launchDurableRun(agentName, inputPayload, versionId),
    onSuccess: (data) => {
      onRunStarted(data.run_id);
    },
  });

  const handleLaunch = () => {
    try {
      const parsed = JSON.parse(payload);
      setParseError(null);
      mutation.mutate(parsed);
    } catch {
      setParseError("Invalid JSON payload");
    }
  };

  return (
    <div className="space-y-3">
      <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wider">
        Input Payload (JSON)
      </label>
      <textarea
        value={payload}
        onChange={(e) => {
          setPayload(e.target.value);
          setParseError(null);
        }}
        className="input font-mono text-sm resize-none"
        rows={6}
        placeholder='{"message": "..."}'
      />
      {parseError && (
        <p className="text-xs text-red-500">{parseError}</p>
      )}
      {mutation.isError && (
        <p className="text-xs text-red-500">
          {mutation.error instanceof Error ? mutation.error.message : "Failed to launch run"}
        </p>
      )}
      <button
        onClick={handleLaunch}
        disabled={mutation.isPending}
        className="btn-primary text-sm"
      >
        {mutation.isPending ? (
          <><Loader2 size={14} className="animate-spin" /> Launching…</>
        ) : (
          <><Play size={14} /> Launch Run</>
        )}
      </button>
    </div>
  );
}
