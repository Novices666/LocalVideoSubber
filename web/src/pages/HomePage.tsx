import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, XCircle, AlertTriangle, RefreshCw, Mic, Languages, Clapperboard, Type, Settings, ArrowRight } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api, type CheckResult, type JobSummary, type JobDetail } from "@/lib/api";
import { cn } from "@/lib/utils";

function StatusIcon({ ok, level }: { ok: boolean; level: string }) {
  if (ok) return <CheckCircle2 className="h-4 w-4 text-accent" aria-hidden />;
  return level === "required" ? (
    <XCircle className="h-4 w-4 text-destructive" aria-hidden />
  ) : (
    <AlertTriangle className="h-4 w-4 text-amber-500" aria-hidden />
  );
}

function CheckList({ result }: { result: CheckResult | null }) {
  if (!result) return null;
  return (
    <div className="scroll-area min-h-0 flex-1 space-y-1 overflow-y-auto">
      {result.items.map((item) => (
        <div
          key={item.name}
          className="flex items-start gap-2 rounded-md px-2 py-1.5 hover:bg-muted/50"
        >
          <span className="mt-0.5 shrink-0">
            <StatusIcon ok={item.ok} level={item.level} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium">{item.name}</span>
              <Badge variant={item.ok ? "secondary" : item.level === "required" ? "destructive" : "warning"}>
                {item.ok ? "通过" : item.level === "required" ? "缺失" : "未就绪"}
              </Badge>
              <span className="text-xs text-muted-foreground">
                {item.level === "required" ? "必需" : "可选"}
              </span>
            </div>
            <div className="truncate text-xs text-muted-foreground">{item.detail}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

const QUICK_LINKS = [
  { to: "/transcribe", label: "转录", desc: "视频转字幕", icon: Mic },
  { to: "/translate", label: "翻译", desc: "字幕翻译", icon: Languages },
  { to: "/render", label: "渲染", desc: "导出与烧录", icon: Clapperboard },
  { to: "/subtitle-settings", label: "字幕设置", desc: "样式规范", icon: Type },
  { to: "/settings", label: "设置", desc: "全局配置", icon: Settings },
];

const KIND_CN: Record<string, string> = {
  transcribe: "转录",
  translate: "翻译",
  render: "渲染",
  resegment: "断句",
};

export function HomePage() {
  const [check, setCheck] = useState<CheckResult | null>(null);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [selectedJob, setSelectedJob] = useState<JobDetail | null>(null);
  const [checking, setChecking] = useState(false);

  const runCheck = useCallback(async () => {
    setChecking(true);
    try {
      setCheck(await api.check());
    } finally {
      setChecking(false);
    }
  }, []);

  const selectJob = useCallback(async (id: string) => {
    try {
      const detail = await api.job(id);
      setSelectedJob(detail);
    } catch {
      /* ignore */
    }
  }, []);

  const loadJobs = useCallback(async () => {
    try {
      const { jobs } = await api.jobs();
      setJobs(jobs.slice(0, 8));
      // 若选中的任务状态更新了，同步刷新详情
      if (selectedJob) {
        const fresh = jobs.find((j) => j.id === selectedJob.id);
        if (fresh && fresh.status !== selectedJob.status) {
          selectJob(fresh.id);
        }
      }
    } catch {
      /* ignore */
    }
  }, [selectedJob, selectJob]);

  useEffect(() => {
    runCheck();
    loadJobs();
    const t = setInterval(loadJobs, 5000);
    return () => clearInterval(t);
  }, [runCheck, loadJobs]);

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display text-xl font-semibold tracking-tight">主页</h1>
          <p className="text-sm text-muted-foreground">系统状态与环境完整性</p>
        </div>
        <Button variant="outline" size="sm" onClick={runCheck} disabled={checking}>
          <RefreshCw className={checking ? "animate-spin" : ""} />
          {checking ? "检测中…" : "重新检测"}
        </Button>
      </div>

      <div className="grid flex-1 grid-cols-2 gap-4">
        <Card className="flex min-h-0 flex-col">
          <CardHeader>
            <CardTitle>环境自检</CardTitle>
            <CardDescription>转录与渲染所依赖的组件完整性</CardDescription>
          </CardHeader>
          <CardContent className="flex min-h-0 flex-1 flex-col">
            <CheckList result={check} />
          </CardContent>
        </Card>

        <Card className="flex min-h-0 flex-col">
          <CardHeader>
            <CardTitle>任务队列</CardTitle>
            <CardDescription>最近提交的任务</CardDescription>
          </CardHeader>
          <CardContent className="min-h-0 flex-1 overflow-y-auto">
            {jobs.length === 0 ? (
              <div className="py-8 text-center text-sm text-muted-foreground">
                暂无任务记录
              </div>
            ) : (
              <div className="space-y-1">
                {jobs.map((j) => (
                  <button
                    key={j.id}
                    onClick={() => selectJob(j.id)}
                    className={cn(
                      "flex w-full items-center gap-3 rounded-md px-2 py-1.5 text-left transition-colors cursor-pointer",
                      selectedJob?.id === j.id
                        ? "bg-primary/10"
                        : "hover:bg-muted/50",
                    )}
                  >
                    <span className="tabular-nums text-xs text-muted-foreground">{j.id}</span>
                    <Badge variant="secondary">{KIND_CN[j.kind] ?? j.kind}</Badge>
                    <span className="min-w-0 flex-1 truncate text-sm">{j.title}</span>
                    <Badge
                      variant={
                        j.status === "已完成"
                          ? "success"
                          : j.status === "失败"
                            ? "destructive"
                            : j.status === "运行中"
                              ? "default"
                              : "secondary"
                      }
                    >
                      {j.status_tag}
                    </Badge>
                    <span className="tabular-nums text-xs text-muted-foreground">
                      {Math.round(j.progress * 100)}%
                    </span>
                  </button>
                ))}
              </div>
            )}
            {selectedJob && (
              <div className="mt-3 border-t border-border pt-3">
                <div className="mb-1 text-sm">
                  <span className="font-medium">{selectedJob.id}</span> · {selectedJob.title}
                </div>
                <div className="mb-2 text-xs text-muted-foreground">
                  {selectedJob.status_tag} · {selectedJob.stage || "-"} · {Math.round(selectedJob.progress * 100)}% · 耗时 {selectedJob.elapsed.toFixed(0)}s
                </div>
                {selectedJob.error && (
                  <div className="mb-2 rounded-md border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">
                    {selectedJob.error}
                  </div>
                )}
                {selectedJob.logs && selectedJob.logs.length > 0 && (
                  <div className="scroll-area max-h-40 overflow-y-auto rounded-md border border-border bg-muted/40 p-2 font-mono text-xs leading-relaxed">
                    {selectedJob.logs.slice(-50).map((line, i) => (
                      <div key={i} className="break-anywhere">{line}</div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <div>
        <h2 className="mb-2 text-base font-medium text-foreground">开始工作</h2>
        <div className="grid grid-cols-5 gap-3">
          {QUICK_LINKS.map(({ to, label, desc, icon: Icon }) => (
            <Link key={to} to={to} className="group">
              <Card className="transition-[border-color,box-shadow] duration-200 hover:border-primary/50 hover:shadow-md">
                <CardContent className="p-4">
                  <div className="mb-2 flex h-8 w-8 items-center justify-center rounded-md bg-primary/10 text-primary">
                    <Icon className="h-4 w-4" aria-hidden />
                  </div>
                  <div className="text-sm font-medium group-hover:text-primary">{label}</div>
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">{desc}</span>
                    <ArrowRight className="h-3.5 w-3.5 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" aria-hidden />
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
