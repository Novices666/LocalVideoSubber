import { useEffect, useState } from "react";
import { Play, Square, PlugZap, Save, FileUp } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Slider } from "@/components/ui/slider";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ProgressPanel } from "@/components/ProgressPanel";
import { CueTable } from "@/components/CueTable";
import { ModelPicker } from "@/components/ModelPicker";
import { DragDrop } from "@/components/DragDrop";
import { useToast } from "@/components/Toast";
import { useJob } from "@/hooks/useJob";
import { useConfig } from "@/hooks/useConfig";
import { useWorkspace } from "@/components/WorkspaceContext";
import { api } from "@/lib/api";
import { getNested, glossaryToText } from "@/lib/utils";

const LANGS = [
  ["auto", "自动检测"], ["zh", "中文"], ["en", "英语"], ["ja", "日语"],
  ["ko", "韩语"], ["fr", "法语"], ["de", "德语"], ["es", "西班牙语"],
  ["ru", "俄语"], ["pt", "葡萄牙语"], ["it", "意大利语"], ["th", "泰语"],
  ["vi", "越南语"], ["ar", "阿拉伯语"],
];

function parseGlossary(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (!t || t.startsWith("#")) continue;
    for (const sep of [":", "：", "=", "→", "->"]) {
      const idx = t.indexOf(sep);
      if (idx > 0) {
        const k = t.slice(0, idx).trim();
        const v = t.slice(idx + sep.length).trim();
        if (k && v) out[k] = v;
        break;
      }
    }
  }
  return out;
}

export function TranslatePage() {
  const { workspace, refresh } = useWorkspace();
  const { state, start, resume, cancel } = useJob();
  const { toast } = useToast();
  const { load, save, saving } = useConfig();

  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:8080/v1");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [llmModelOptions, setLlmModelOptions] = useState<string[]>([]);
  const [refreshingModels, setRefreshingModels] = useState(false);
  const [sourceLang, setSourceLang] = useState("auto");
  const [targetLang, setTargetLang] = useState("zh");
  const [batch, setBatch] = useState(25);
  const [context, setContext] = useState(3);
  const [concurrency, setConcurrency] = useState(1);
  const [temperature, setTemperature] = useState(0.3);
  const [disableThinking, setDisableThinking] = useState(true);
  const [glossary, setGlossary] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");

  const [testMsg, setTestMsg] = useState("");
  const [testing, setTesting] = useState(false);
  const [importPath, setImportPath] = useState("");

  async function refreshModels(url?: string, key?: string, silent = false) {
    setRefreshingModels(true);
    try {
      const r = await api.llmModels(url ?? baseUrl, key ?? apiKey);
      setLlmModelOptions(r.models);
      if (r.detail && !silent) setTestMsg(r.detail);
    } finally {
      setRefreshingModels(false);
    }
  }

  async function testConnection() {
    if (!baseUrl) {
      setTestMsg("请先填写服务地址");
      return;
    }
    setTesting(true);
    setTestMsg("");
    try {
      const r = await api.llmTest(baseUrl, apiKey, model);
      setTestMsg(r.ok ? `连接成功 — ${r.detail}` : `连接失败 — ${r.detail}`);
    } catch (e) {
      setTestMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setTesting(false);
    }
  }

  function submit() {
    start(() =>
      api.translate({
        base_url: baseUrl,
        api_key: apiKey,
        model,
        source_lang: sourceLang,
        target_lang: targetLang,
        batch_size: batch,
        context_size: context,
        concurrency,
        temperature,
        disable_thinking: disableThinking,
        glossary: parseGlossary(glossary),
        system_prompt: systemPrompt || undefined,
      }),
    );
  }

  async function savePage() {
    await save({
      "translate.base_url": baseUrl,
      "translate.api_key": apiKey,
      "translate.model": model,
      "translate.source_lang": sourceLang,
      "translate.target_lang": targetLang,
      "translate.batch_size": batch,
      "translate.context_size": context,
      "translate.concurrency": concurrency,
      "translate.temperature": temperature,
      "translate.disable_thinking": disableThinking,
      "translate.glossary": parseGlossary(glossary),
      "translate.system_prompt": systemPrompt,
    });
  }

  async function importCues(pathOverride?: string) {
    const target = pathOverride || importPath;
    if (!target) return;
    try {
      const r = await api.loadCues(target, false);
      toast(`已导入 ${r.count} 条字幕`, "success");
      setImportPath(target);
      refresh();
    } catch (e) {
      toast(e instanceof Error ? e.message : "导入失败", "error");
    }
  }

  useEffect(() => {
    load().then((v) => {
      const realBase = String(getNested(v, "translate.base_url", "") ?? "");
      const realKey = String(getNested(v, "translate.api_key", "") ?? "");
      setBaseUrl(realBase || "http://127.0.0.1:8080/v1");
      setApiKey(realKey);
      setModel(String(getNested(v, "translate.model", "") ?? ""));
      setSourceLang(String(getNested(v, "translate.source_lang", "auto")));
      setTargetLang(String(getNested(v, "translate.target_lang", "zh")));
      setBatch(Number(getNested(v, "translate.batch_size", 25)));
      setContext(Number(getNested(v, "translate.context_size", 3)));
      setConcurrency(Number(getNested(v, "translate.concurrency", 1)));
      setTemperature(Number(getNested(v, "translate.temperature", 0.3)));
      setDisableThinking(Boolean(getNested(v, "translate.disable_thinking", true)));
      setGlossary(glossaryToText(getNested(v, "translate.glossary", undefined) as Record<string, string> | undefined));
      setSystemPrompt(String(getNested(v, "translate.system_prompt", "") ?? ""));
      // 用真实配置静默拉取模型列表，失败不打扰用户（手动刷新时才报错）
      if (realBase) refreshModels(realBase, realKey, true);
    });
    // 刷新页面后，若后端有运行中的翻译任务，自动恢复连接与进度显示
    api.jobs().then(({ jobs }) => {
      const running = jobs.find(
        (j) => j.kind === "translate" && (j.status === "运行中" || j.status === "排队中"),
      );
      if (running) resume(running.id);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex h-full">
      {/* 左栏：配置 */}
      <div className="scroll-area w-[400px] shrink-0 space-y-4 overflow-y-auto border-r border-border p-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle>字幕来源</CardTitle>
            <CardDescription>
              当前 {workspace?.cue_count ?? 0} 条字幕。可独立导入，无需先转录
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <DragDrop
              label="拖入字幕文件（srt / vtt / ass）"
              hint="上传后自动导入为当前字幕"
              accept=".srt,.vtt,.ass,.ssa"
              compact
              onUpload={(path) => importCues(path)}
            />
            <div className="flex gap-2">
              <Input
                value={importPath}
                onChange={(e) => setImportPath(e.target.value)}
                placeholder="或手动输入字幕文件完整路径"
              />
              <Button variant="outline" onClick={() => importCues()}>
                <FileUp />
                导入
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle>翻译服务</CardTitle>
            <CardDescription>OpenAI 兼容接口，以 /v1 结尾</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="space-y-1.5">
              <Label>服务地址 base_url</Label>
              <div className="flex gap-2">
                <Input
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="http://127.0.0.1:8080/v1"
                  className="flex-1"
                />
                <Button variant="outline" onClick={testConnection} disabled={testing || !baseUrl}>
                  <PlugZap />
                  {testing ? "测试中…" : "测试连接"}
                </Button>
              </div>
            </div>
            {testMsg && (
              <div className="rounded-md border border-border bg-muted/40 p-2 text-xs">
                {testMsg}
              </div>
            )}
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1.5">
                <Label>API Key</Label>
                <Input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="本地服务可留空"
                />
              </div>
              <div className="space-y-1.5">
                <Label>模型名</Label>
                <ModelPicker
                  value={model}
                  onChange={setModel}
                  options={llmModelOptions}
                  onRefresh={() => refreshModels()}
                  refreshing={refreshingModels}
                  placeholder="留空用服务端第一个"
                />
              </div>
            </div>
            <div className="flex items-center justify-between">
              <Label htmlFor="no-think">关闭思考模式（Qwen3.5 等推理模型建议开启）</Label>
              <Switch id="no-think" checked={disableThinking} onCheckedChange={setDisableThinking} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle>语言</CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-2 gap-2">
            <div className="space-y-1.5">
              <Label>源语言</Label>
              <Select value={sourceLang} onValueChange={setSourceLang}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {LANGS.map(([v, l]) => (
                    <SelectItem key={v} value={v}>
                      {l}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>目标语言</Label>
              <Select value={targetLang} onValueChange={setTargetLang}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {LANGS.filter(([v]) => v !== "auto").map(([v, l]) => (
                    <SelectItem key={v} value={v}>
                      {l}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle>批量与采样</CardTitle>
            <CardDescription>影响翻译质量与速度的平衡</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label>每批条数</Label>
                <span className="tabular-nums text-xs text-muted-foreground">{batch}</span>
              </div>
              <Slider value={[batch]} min={5} max={60} step={1} onValueChange={(v) => setBatch(v[0])} />
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label>上下文条数</Label>
                <span className="tabular-nums text-xs text-muted-foreground">{context}</span>
              </div>
              <Slider value={[context]} min={0} max={10} step={1} onValueChange={(v) => setContext(v[0])} />
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label>并发请求（单卡建议 1-2）</Label>
                <span className="tabular-nums text-xs text-muted-foreground">{concurrency}</span>
              </div>
              <Slider value={[concurrency]} min={1} max={8} step={1} onValueChange={(v) => setConcurrency(v[0])} />
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label>temperature（越低越稳定）</Label>
                <span className="tabular-nums text-xs text-muted-foreground">{temperature.toFixed(2)}</span>
              </div>
              <Slider value={[temperature]} min={0} max={1} step={0.05} onValueChange={(v) => setTemperature(v[0])} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle>术语表与提示词</CardTitle>
            <CardDescription>每行一条：原文: 译文</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Textarea
              value={glossary}
              onChange={(e) => setGlossary(e.target.value)}
              placeholder={"Naruto: 鸣人\nJutsu: 忍术"}
              rows={4}
            />
            <Textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder="自定义系统提示词（留空用内置）"
              rows={3}
            />
          </CardContent>
        </Card>

        <div className="space-y-2 pb-4">
          <div className="flex gap-2">
            {state.running ? (
              <Button variant="destructive" className="flex-1" onClick={cancel}>
                <Square />
                取消任务
              </Button>
            ) : (
              <Button
                className="flex-1"
                onClick={submit}
                disabled={(workspace?.cue_count ?? 0) === 0}
              >
                <Play />
                开始翻译
              </Button>
            )}
            <Button variant="outline" onClick={savePage} disabled={saving}>
              <Save />
              保存设置
            </Button>
          </div>
        </div>
      </div>

      {/* 右栏：进度 + 译文表 */}
      <div className="flex min-w-0 flex-1 flex-col gap-4 p-4">
        <Card className="h-52 shrink-0">
          <CardHeader className="pb-2">
            <CardTitle>进度</CardTitle>
          </CardHeader>
          <CardContent className="h-[calc(100%-3rem)]">
            <ProgressPanel progress={state.progress} error={state.error} done={!!state.done} />
          </CardContent>
        </Card>

        <Card className="min-h-0 flex-1">
          <CardHeader className="pb-2">
            <CardTitle>译文</CardTitle>
            <CardDescription>编辑译文列，改完点「保存修改」</CardDescription>
          </CardHeader>
          <CardContent className="h-[calc(100%-4rem)]">
            <CueTable
              cues={workspace?.cues ?? []}
              editable={{ start: false, end: false, text: false, translation: true }}
              onSave={async (cues) => {
                await api.updateCues(cues);
                refresh();
              }}
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
