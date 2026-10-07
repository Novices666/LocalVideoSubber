import { useEffect, useState } from "react";
import { Save, RotateCcw } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Slider } from "@/components/ui/slider";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { ModelPicker } from "@/components/ModelPicker";

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
  const [values, setValues] = useState<ConfigValues>({});
  const [meta, setMeta] = useState<{ path: string; model_root: string; output_dir: string } | null>(null);

  const [modelRoot, setModelRoot] = useState("./models");
  const [asrModel, setAsrModel] = useState("");
  const [asrModelOptions, setAsrModelOptions] = useState<string[]>([]);
  const [refreshingAsr, setRefreshingAsr] = useState(false);
  const [asrDevice, setAsrDevice] = useState("auto");
  const [asrCompute, setAsrCompute] = useState("float16");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [llmModel, setLlmModel] = useState("");
  const [llmModelOptions, setLlmModelOptions] = useState<string[]>([]);
  const [refreshingLlm, setRefreshingLlm] = useState(false);
  const [disableThinking, setDisableThinking] = useState(true);
  const [temperature, setTemperature] = useState(0.3);
  const [batch, setBatch] = useState(25);
  const [context, setContext] = useState(3);
  const [concurrency, setConcurrency] = useState(1);
  const [outDir, setOutDir] = useState("./output");
  const [keepIntermediate, setKeepIntermediate] = useState(true);
  const [fontName, setFontName] = useState("Microsoft YaHei");
  const [fontSize, setFontSize] = useState(42);
  const [marginV, setMarginV] = useState(40);
  const [encoder, setEncoder] = useState("auto");
  const [crf, setCrf] = useState(20);

  const [msg, setMsg] = useState("");

  async function load() {
    try {
      const cfg = await api.config();
      setMeta({ path: cfg.path, model_root: cfg.model_root, output_dir: cfg.output_dir });
      const v = cfg.values;
      setValues(v);
      setModelRoot(String(getNested(v, "model_root", "./models")));
      setAsrModel(String(getNested(v, "asr.model", "") ?? ""));
      setAsrDevice(String(getNested(v, "asr.device", "auto")));
      setAsrCompute(String(getNested(v, "asr.compute_type", "float16")));
      setBaseUrl(String(getNested(v, "translate.base_url", "") ?? ""));
      setApiKey(String(getNested(v, "translate.api_key", "") ?? ""));
      setLlmModel(String(getNested(v, "translate.model", "") ?? ""));
      setDisableThinking(Boolean(getNested(v, "translate.disable_thinking", true)));
      setTemperature(Number(getNested(v, "translate.temperature", 0.3)));
      setBatch(Number(getNested(v, "translate.batch_size", 25)));
      setContext(Number(getNested(v, "translate.context_size", 3)));
      setConcurrency(Number(getNested(v, "translate.concurrency", 1)));
      setOutDir(String(getNested(v, "jobs.output_dir", "./output")));
      setKeepIntermediate(Boolean(getNested(v, "jobs.keep_intermediate", true)));
      setFontName(String(getNested(v, "render.style.font_name", "Microsoft YaHei")));
      setFontSize(Number(getNested(v, "render.style.font_size", 42)));
      setMarginV(Number(getNested(v, "render.style.margin_v", 40)));
      setEncoder(String(getNested(v, "render.burn_encoder", "auto")));
      setCrf(Number(getNested(v, "render.burn_crf", 20)));
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "加载失败");
    }
  }

  useEffect(() => {
    load();
    refreshAsrModels();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function refreshAsrModels() {
    setRefreshingAsr(true);
    try {
      const m = await api.models();
      setAsrModelOptions(m.asr.map((x) => x.value));
    } finally {
      setRefreshingAsr(false);
    }
  }

  async function refreshLlmModels() {
    setRefreshingLlm(true);
    try {
      const r = await api.llmModels(baseUrl, apiKey);
      setLlmModelOptions(r.models);
    } finally {
      setRefreshingLlm(false);
    }
  }

  async function save() {
    try {
      const res = await api.saveConfig({
        model_root: modelRoot,
        "asr.model": asrModel,
        "asr.device": asrDevice,
        "asr.compute_type": asrCompute,
        "translate.base_url": baseUrl,
        "translate.api_key": apiKey,
        "translate.model": llmModel,
        "translate.disable_thinking": disableThinking,
        "translate.temperature": temperature,
        "translate.batch_size": batch,
        "translate.context_size": context,
        "translate.concurrency": concurrency,
        "jobs.output_dir": outDir,
        "jobs.keep_intermediate": keepIntermediate,
        "render.burn_encoder": encoder,
        "render.burn_crf": crf,
        "render.style.font_name": fontName,
        "render.style.font_size": fontSize,
        "render.style.margin_v": marginV,
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
            {meta ? `配置文件 ${meta.path} · 模型目录 ${meta.model_root}` : ""}
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
        <Tabs defaultValue="models" className="w-full">
          <TabsList>
            <TabsTrigger value="models">模型与路径</TabsTrigger>
            <TabsTrigger value="translate">翻译服务</TabsTrigger>
            <TabsTrigger value="output">输出与渲染</TabsTrigger>
          </TabsList>

          <TabsContent value="models">
            <Card>
              <CardHeader>
                <CardTitle>模型与路径</CardTitle>
                <CardDescription>相对模型根目录、绝对路径、或仓库名</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-1.5">
                  <Label>模型根目录</Label>
                  <Input value={modelRoot} onChange={(e) => setModelRoot(e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label>默认 ASR 模型</Label>
                  <ModelPicker
                    value={asrModel}
                    onChange={setAsrModel}
                    options={asrModelOptions}
                    onRefresh={refreshAsrModels}
                    refreshing={refreshingAsr}
                    placeholder="asr/faster-whisper-large-v3"
                  />
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-1.5">
                    <Label>运行设备</Label>
                    <Select value={asrDevice} onValueChange={setAsrDevice}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="auto">自动</SelectItem>
                        <SelectItem value="cuda">GPU (CUDA)</SelectItem>
                        <SelectItem value="cpu">CPU</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <Label>计算精度</Label>
                    <Select value={asrCompute} onValueChange={setAsrCompute}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="auto">自动</SelectItem>
                        <SelectItem value="int8_float16">int8_float16</SelectItem>
                        <SelectItem value="float16">float16</SelectItem>
                        <SelectItem value="int8">int8</SelectItem>
                        <SelectItem value="float32">float32</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="translate">
            <Card>
              <CardHeader>
                <CardTitle>翻译服务</CardTitle>
                <CardDescription>OpenAI 兼容接口的默认值</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-1.5">
                    <Label>服务地址 base_url</Label>
                    <Input
                      value={baseUrl}
                      onChange={(e) => setBaseUrl(e.target.value)}
                      placeholder="http://127.0.0.1:8080/v1"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label>模型名</Label>
                    <ModelPicker
                      value={llmModel}
                      onChange={setLlmModel}
                      options={llmModelOptions}
                      onRefresh={refreshLlmModels}
                      refreshing={refreshingLlm}
                      placeholder="留空用服务端第一个"
                    />
                  </div>
                </div>
                <div className="space-y-1.5">
                  <Label>API Key</Label>
                  <Input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
                </div>
                <div className="flex items-center justify-between">
                  <Label htmlFor="dt">关闭思考模式</Label>
                  <Switch id="dt" checked={disableThinking} onCheckedChange={setDisableThinking} />
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between">
                      <Label>temperature</Label>
                      <span className="tabular-nums text-xs text-muted-foreground">{temperature.toFixed(2)}</span>
                    </div>
                    <Slider value={[temperature]} min={0} max={1} step={0.05} onValueChange={(v) => setTemperature(v[0])} />
                  </div>
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
                      <Label>并发请求</Label>
                      <span className="tabular-nums text-xs text-muted-foreground">{concurrency}</span>
                    </div>
                    <Slider value={[concurrency]} min={1} max={8} step={1} onValueChange={(v) => setConcurrency(v[0])} />
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="output">
            <Card>
              <CardHeader>
                <CardTitle>输出与渲染</CardTitle>
                <CardDescription>产物位置与字幕样式默认值</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-1.5">
                  <Label>输出目录</Label>
                  <Input value={outDir} onChange={(e) => setOutDir(e.target.value)} />
                </div>
                <div className="flex items-center justify-between">
                  <Label htmlFor="keep">保留中间产物（音频、分段字幕）</Label>
                  <Switch id="keep" checked={keepIntermediate} onCheckedChange={setKeepIntermediate} />
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-1.5">
                    <Label>默认编码器</Label>
                    <Select value={encoder} onValueChange={setEncoder}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {["auto", "h264_nvenc", "hevc_nvenc", "libx264", "libx265"].map((e) => (
                          <SelectItem key={e} value={e}>
                            {e}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between">
                      <Label>默认质量参数</Label>
                      <span className="tabular-nums text-xs text-muted-foreground">{crf}</span>
                    </div>
                    <Slider value={[crf]} min={10} max={35} step={1} onValueChange={(v) => setCrf(v[0])} />
                  </div>
                </div>
                <div className="space-y-1.5">
                  <Label>默认字体</Label>
                  <Input value={fontName} onChange={(e) => setFontName(e.target.value)} />
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between">
                      <Label>默认字号</Label>
                      <span className="tabular-nums text-xs text-muted-foreground">{fontSize}</span>
                    </div>
                    <Slider value={[fontSize]} min={16} max={80} step={1} onValueChange={(v) => setFontSize(v[0])} />
                  </div>
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between">
                      <Label>默认距底边距</Label>
                      <span className="tabular-nums text-xs text-muted-foreground">{marginV}</span>
                    </div>
                    <Slider value={[marginV]} min={0} max={200} step={5} onValueChange={(v) => setMarginV(v[0])} />
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>

        {msg && (
          <div className="mt-4 rounded-md border border-border bg-muted/50 p-3 text-sm">
            {msg}
          </div>
        )}
      </div>
    </div>
  );
}
