import { useEffect, useRef } from "react";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import type { ProgressEvent } from "@/lib/api";

const STATUS_VARIANT: Record<string, "default" | "secondary" | "success" | "destructive" | "warning"> = {
  排队中: "secondary",
  运行中: "default",
  已完成: "success",
  失败: "destructive",
  已取消: "warning",
};

export function ProgressPanel({
  progress,
  error,
  done,
}: {
  progress: ProgressEvent | null;
  error: string | null;
  done: boolean;
}) {
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [progress?.logs?.length]);

  if (!progress && !error && !done) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        提交任务后，进度和日志会显示在这里。
      </div>
    );
  }

  const pct = Math.round((progress?.progress ?? 0) * 100);
  const logs = progress?.logs ?? [];

  return (
    <div className="flex h-full flex-col gap-3">
      <div className="flex items-center gap-3">
        <Progress value={pct} className="flex-1" />
        <span className="tabular-nums text-sm font-medium">{pct}%</span>
        {progress && (
          <Badge variant={STATUS_VARIANT[progress.status] ?? "secondary"}>
            {progress.status_tag}
          </Badge>
        )}
      </div>

      {(progress?.stage || progress?.message || error) && (
        <div className="text-sm">
          {progress?.stage && <span className="font-medium">{progress.stage}</span>}
          {progress?.message && (
            <span className="text-muted-foreground"> · {progress.message}</span>
          )}
          {progress?.elapsed ? (
            <span className="ml-2 tabular-nums text-xs text-muted-foreground">
              {progress.elapsed.toFixed(0)}s
            </span>
          ) : null}
        </div>
      )}

      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 p-2 text-sm text-destructive">
          {error}
        </div>
      )}

      {logs.length > 0 && (
        <div
          ref={logRef}
          className="scroll-area flex-1 overflow-y-auto rounded-md border border-border bg-muted/40 p-2 font-mono text-xs leading-relaxed"
        >
          {logs.map((line, i) => (
            <div key={i} className="whitespace-pre-wrap break-anywhere">
              {line}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
