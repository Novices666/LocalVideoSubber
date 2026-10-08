import { useEffect, useState } from "react";
import { Save, RotateCcw } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { api } from "@/lib/api";

type ConfigValues = Record<string, unknown>;

function getNested(obj: ConfigValues, dotted: string, fallback: unknown): unknown {
  let node: unknown = obj;
  for (const key of dotted.split(".")) {
    if (node && typeof node === "object" && key in (node as object)) {
      node = (node as Record<string, unknown>)[key];
    } else {
      return fallback;
    }
  }
  return node ?? fallback;
}

export function SettingsPage() {
  const [meta, setMeta] = useState<{ path: string; model_root: string; output_dir: string } | null>(null);

  const [modelRoot, setModelRoot] = useState("./models");
  const [timeoutSec, setTimeoutSec] = useState(300);
  const [outDir, setOutDir] = useState("./workspace");
  const [keepIntermediate, setKeepIntermediate] = useState(true);

  const [msg, setMsg] = useState("");

  async function load() {
    try {
      const cfg = await api.config();
      setMeta({ path: cfg.path, model_root: cfg.model_root, output_dir: cfg.output_dir });
      const v = cfg.values;
      setModelRoot(String(getNested(v, "model_root", "./models")));
      setTimeoutSec(Number(getNested(v, "translate.timeout", 300)));
      setOutDir(String(getNested(v, "jobs.output_dir", "./workspace")));
      setKeepIntermediate(Boolean(getNested(v, "jobs.keep_intermediate", true)));
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "加载失败");
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function save() {
    try {
      const res = await api.saveConfig({
        model_root: modelRoot,
        "translate.timeout": timeoutSec,
        "jobs.output_dir": outDir,
        "jobs.keep_intermediate": keepIntermediate,
      });
      setMsg(`已保存到 ${res.path}，重启后生效`);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "保存失败");
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-6 py-3">
        <div>
          <h1 className="font-display text-xl font-semibold">设置</h1>
          <p className="text-xs text-muted-foreground">
            {meta ? `配置文件 ${meta.path}` : ""}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={load}>
            <RotateCcw />
            重新载入
          </Button>
          <Button size="sm" onClick={save}>
            <Save />
            保存设置
          </Button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-6">
        <Card className="max-w-2xl">
          <CardHeader>
            <CardTitle>全局设置</CardTitle>
            <CardDescription>
              模型目录、工作目录等全局项。模型、翻译参数、样式请在各自页面设置。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label>模型根目录</Label>
              <Input value={modelRoot} onChange={(e) => setModelRoot(e.target.value)} />
              <p className="text-xs text-muted-foreground">
                ASR 模型存放目录，相对项目根或绝对路径
              </p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="timeout">翻译请求超时（秒）</Label>
              <Input
                id="timeout"
                type="number"
                min={10}
                value={timeoutSec}
                onChange={(e) => setTimeoutSec(Number(e.target.value) || 300)}
              />
              <p className="text-xs text-muted-foreground">
                单次翻译请求等待上限。本地模型思考较慢时可调大（如 600 / 1800）
              </p>
            </div>

            <div className="space-y-1.5">
              <Label>工作目录</Label>
              <Input value={outDir} onChange={(e) => setOutDir(e.target.value)} />
              <p className="text-xs text-muted-foreground">
                按视频分组的 workspace：upload / transcribe / translate / burn / output
              </p>
            </div>

            <div className="flex items-center justify-between">
              <div>
                <Label htmlFor="keep">保留中间产物</Label>
                <p className="text-xs text-muted-foreground">
                  转录的音频、中间字幕等，关闭后任务结束自动清理
                </p>
              </div>
              <Switch id="keep" checked={keepIntermediate} onCheckedChange={setKeepIntermediate} />
            </div>
          </CardContent>
        </Card>

        {msg && (
          <div className="mt-4 max-w-2xl rounded-md border border-border bg-muted/50 p-3 text-sm">
            {msg}
          </div>
        )}
      </div>
    </div>
  );
}
