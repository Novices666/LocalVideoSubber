import { useCallback, useRef, useState } from "react";
import {
  subscribeJobEvents,
  type DoneEvent,
  type ProgressEvent,
} from "@/lib/api";
import { useWorkspace } from "@/components/WorkspaceContext";

export interface JobRunState {
  running: boolean;
  jobId: string | null;
  progress: ProgressEvent | null;
  done: DoneEvent | null;
  error: string | null;
}

export function useJob() {
  const { refresh } = useWorkspace();
  const [state, setState] = useState<JobRunState>({
    running: false,
    jobId: null,
    progress: null,
    done: null,
    error: null,
  });
  const closeRef = useRef<(() => void) | null>(null);

  const start = useCallback(
    async (submit: () => Promise<{ job_id: string }>) => {
      closeRef.current?.();
      setState({ running: true, jobId: null, progress: null, done: null, error: null });
      try {
        const { job_id } = await submit();
        setState((s) => ({ ...s, jobId: job_id }));
        const close = subscribeJobEvents(
          job_id,
          (progress) => setState((s) => ({ ...s, progress })),
          (done) => {
            setState((s) => ({ ...s, running: false, done }));
            closeRef.current = null;
            refresh();
          },
          () => {
            setState((s) => ({ ...s, running: false }));
          },
        );
        closeRef.current = close;
      } catch (e) {
        setState({
          running: false,
          jobId: null,
          progress: null,
          done: null,
          error: e instanceof Error ? e.message : String(e),
        });
      }
    },
    [refresh],
  );

  const cancel = useCallback(async () => {
    if (!state.jobId) return;
    try {
      await fetch(`/api/jobs/${state.jobId}/cancel`, { method: "POST" });
    } catch {
      /* ignore */
    }
  }, [state.jobId]);

  return { state, start, cancel };
}
