import { useEffect, useState } from "react";
import { Play, Square, FileSearch, Loader2, Save } from "lucide-react";
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
import { ModelPicker, type ModelOption } from "@/components/ModelPicker";
import { DragDrop } from "@/components/DragDrop";
import { useJob } from "@/hooks/useJob";
import { useConfig } from "@/hooks/useConfig";
import { useWorkspace } from "@/components/WorkspaceContext";
import { api, type MediaInfo } from "@/lib/api";
import { getNested } from "@/lib/utils";

const LANGS = [
  ["auto", "自动检测"], ["zh", "中文"], ["en", "英语"], ["ja", "日语"],
  ["ko", "韩语"], ["fr", "法语"], ["de", "德语"], ["es", "西班牙语"],
  ["ru", "俄语"], ["pt", "葡萄牙语"], ["it", "意大利语"], ["th", "泰语"],
  ["vi", "越南语"], ["ar", "阿拉伯语"],
];

export function TranscribePage() {
  const { workspace, refresh } = useWorkspace();
  const { state, start, resume, cancel } = useJob();
  const { load, save, saving } = useConfig();

  const [asrOptions, setAsrOptions] = useState<ModelOption[]>([]);
  const [refreshingModels, setRefreshingModels] = useState(false);
  const [video, setVideo] = useState("");
  const [media, setMedia] = useState<MediaInfo | null>(null);
  const [probing, setProbing] = useState(false);
  const [probeError, setProbeError] = useState("");

  const [model, setModel] = useState("");
  const [engine, setEngine] = useState("faster-whisper");
  const [language, setLanguage] = useState("auto");
  const [task, setTask] = useState("transcribe");
  const [device, setDevice] = useState("auto");
  const [compute, setCompute] = useState("float16");
  const [beam, setBeam] = useState(5);
  const [vad, setVad] = useState(true);
  const [prompt, setPrompt] = useState("");
  const [maxCjk, setMaxCjk] = useState(18);
  const [maxLatin, setMaxLatin] = useState(42);
  const [minDur, setMinDur] = useState(0.8);
  const [maxDur, setMaxDur] = useState(8.0);

  async function refreshModels() {
    setRefreshingModels(true);
    try {
      const m = await api.models();
      setAsrOptions(m.asr.map((x) => ({ value: x.value, label: x.label })));
      // 若还没选模型，自动选中第一个，避免每次打开都要手动选
      setModel((cur) => cur || (m.asr[0]?.value ?? ""));
    } finally {
      setRefreshingModels(false);
    }
  }

  useEffect(() => {
    refreshModels();
    load().then((v) => {
      setEngine(String(getNested(v, "asr.engine", "faster-whisper")));
      setLanguage(String(getNested(v, "asr.language", "auto")));
      setTask(String(getNested(v, "asr.task", "transcribe")));
      setDevice(String(getNested(v, "asr.device", "auto")));
      setCompute(String(getNested(v, "asr.compute_type", "float16")));
      setBeam(Number(getNested(v, "asr.beam_size", 5)));
      setVad(Boolean(getNested(v, "asr.vad_filter", true)));
      setPrompt(String(getNested(v, "asr.initial_prompt", "") ?? ""));
      setMaxCjk(Number(getNested(v, "segment.max_chars_cjk", 18)));
      setMaxLatin(Number(getNested(v, "segment.max_chars_latin", 42)));
      setMinDur(Number(getNested(v, "segment.min_duration", 0.8)));
      setMaxDur(Number(getNested(v, "segment.max_duration", 8.0)));
    });
    // 刷新页面后，若后端有运行中的转录任务，自动恢复连接与进度显示
    api.jobs().then(({ jobs }) => {
      const running = jobs.find(
        (j) => j.kind === "transcribe" && (j.status === "运行中" || j.status === "排队中"),
      );
      if (running) resume(running.id);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function probe(targetPath?: string) {
    const target = targetPath || video;
    if (!target) return;
    setProbing(true);
    setProbeError("");
    try {
      setMedia(await api.probe(target));
    } catch (e) {
      setProbeError(e instanceof Error ? e.message : String(e));
      setMedia(null);
    } finally {
      setProbing(false);
    }
  }

  function submit() {
    if (!video) return;
    start(() =>
      api.transcribe({
        video,
        model_path: model || null,
        engine,
        language,
        task,
        beam_size: beam,
        device,
        compute_type: compute,
        vad_filter: vad,
        initial_prompt: prompt || undefined,
        seg_opts: {
          enabled: true,
          max_chars_cjk: maxCjk,
          max_chars_latin: maxLatin,
          min_duration: minDur,
          max_duration: maxDur,
        },
      }),
    );
  }

  async function savePage() {
    await save({
      "asr.engine": engine,
      "asr.model": model || undefined,
      "asr.language": language,
      "asr.task": task,
      "asr.device": device,
      "asr.compute_type": compute,
      "asr.beam_size": beam,
      "asr.vad_filter": vad,
      "asr.initial_prompt": prompt,
      "segment.max_chars_cjk": maxCjk,
      "segment.max_chars_latin": maxLatin,
      "segment.min_duration": minDur,
      "segment.max_duration": maxDur,
    });
  }

  return (
    <div className="flex h-full">
      {/* 左栏：配置 */}
      <div className="scroll-area w-[400px] shrink-0 space-y-4 overflow-y-auto border-r border-border p-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle>素材</CardTitle>
            <CardDescription>本地视频或音频文件的完整路径</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <DragDrop
              label="拖入视频或音频，或点击选择"
              hint="上传后保存到 output/uploads，并自动填入下方路径"
              accept=".mp4,.mkv,.avi,.mov,.flv,.wmv,.webm,.m4v,.mp3,.wav,.m4a,.aac,.flac,.ogg"
              onUpload={(path) => {
                setVideo(path);
                setMedia(null);
                setProbeError("");
                probe(path);
              }}
            />
            <div className="flex gap-2">
              <Input
                value={video}
                onChange={(e) => setVideo(e.target.value)}
                placeholder="或手动输入完整路径，例如 F:\\videos\\ep01.mp4"
              />
              <Button variant="outline" size="icon" onClick={() => probe()} disabled={probing || !video}>
                {probing ? <Loader2 className="animate-spin" /> : <FileSearch />}
              </Button>
            </div>
            {probeError && (
              <div className="text-xs text-destructive">{probeError}</div>
            )}
            {media && (
              <div className="rounded-md bg-muted/50 p-2 text-xs leading-relaxed">
                <div className="font-medium">{media.path.split(/[\\/]/).pop()}</div>
                <div className="text-muted-foreground">
                  时长 {media.duration_text}
                  {media.has_video ? ` · ${media.resolution} ${media.fps.toFixed(0)}fps` : " · 纯音频"}
                  {media.has_audio ? "" : " · 无音轨"}
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle>识别模型</CardTitle>
            <CardDescription>扫描自 models/asr 目录</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="space-y-1.5">
              <Label>模型</Label>
              <ModelPicker
                value={model}
                onChange={setModel}
                options={asrOptions}
                onRefresh={refreshModels}
                refreshing={refreshingModels}
                placeholder="选择或输入模型路径 / 仓库名"
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1.5">
                <Label>引擎</Label>
                <Select value={engine} onValueChange={setEngine}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="faster-whisper">faster-whisper</SelectItem>
                    <SelectItem value="whispercpp">whisper.cpp</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>任务</Label>
                <Select value={task} onValueChange={setTask}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="transcribe">原语言转录</SelectItem>
                    <SelectItem value="translate">直译成英文</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1.5">
                <Label>源语言</Label>
                <Select value={language} onValueChange={setLanguage}>
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
                <Label>设备</Label>
                <Select value={device} onValueChange={setDevice}>
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
            </div>
            <div className="space-y-1.5">
              <Label>计算精度</Label>
              <Select value={compute} onValueChange={setCompute}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="auto">自动</SelectItem>
                  <SelectItem value="int8_float16">int8_float16（省显存）</SelectItem>
                  <SelectItem value="float16">float16</SelectItem>
                  <SelectItem value="int8">int8</SelectItem>
                  <SelectItem value="float32">float32</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label>beam_size（越大越准越慢）</Label>
                <span className="tabular-nums text-xs text-muted-foreground">{beam}</span>
              </div>
              <Slider value={[beam]} min={1} max={10} step={1} onValueChange={(v) => setBeam(v[0])} />
            </div>
            <div className="flex items-center justify-between">
              <Label htmlFor="vad">VAD 静音过滤</Label>
              <Switch id="vad" checked={vad} onCheckedChange={setVad} />
            </div>
            <div className="space-y-1.5">
              <Label>初始提示词（提升专有名词识别率，可选）</Label>
              <Textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="本视频涉及 Transformer、CUDA、量化"
                rows={2}
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle>断句规则</CardTitle>
            <CardDescription>控制字幕的切分粒度</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label>中文单行最长字数</Label>
                <span className="tabular-nums text-xs text-muted-foreground">{maxCjk}</span>
              </div>
              <Slider value={[maxCjk]} min={8} max={40} step={1} onValueChange={(v) => setMaxCjk(v[0])} />
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label>英文单行最长字符</Label>
                <span className="tabular-nums text-xs text-muted-foreground">{maxLatin}</span>
              </div>
              <Slider value={[maxLatin]} min={20} max={90} step={1} onValueChange={(v) => setMaxLatin(v[0])} />
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label>单条最短时长（秒）</Label>
                <span className="tabular-nums text-xs text-muted-foreground">{minDur.toFixed(1)}</span>
              </div>
              <Slider value={[minDur]} min={0.3} max={3} step={0.1} onValueChange={(v) => setMinDur(v[0])} />
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label>单条最长时长（秒）</Label>
                <span className="tabular-nums text-xs text-muted-foreground">{maxDur.toFixed(1)}</span>
              </div>
              <Slider value={[maxDur]} min={2} max={20} step={0.5} onValueChange={(v) => setMaxDur(v[0])} />
            </div>
          </CardContent>
        </Card>

        <div className="flex gap-2 pb-4">
          {state.running ? (
            <Button variant="destructive" className="flex-1" onClick={cancel}>
              <Square />
              取消任务
            </Button>
          ) : (
            <Button className="flex-1" onClick={submit} disabled={!video}>
              <Play />
              开始转录
            </Button>
          )}
          <Button variant="outline" onClick={savePage} disabled={saving}>
            <Save />
            保存设置
          </Button>
        </div>
      </div>

      {/* 右栏：进度 + 字幕 */}
      <div className="flex min-w-0 flex-1 flex-col gap-4 p-4">
        <Card className="h-64 shrink-0">
          <CardHeader className="pb-2">
            <CardTitle>进度</CardTitle>
          </CardHeader>
          <CardContent className="h-[calc(100%-3rem)]">
            <ProgressPanel progress={state.progress} error={state.error} done={!!state.done} />
          </CardContent>
        </Card>

        <Card className="min-h-0 flex-1">
          <CardHeader className="pb-2">
            <CardTitle>字幕</CardTitle>
            <CardDescription>可直接编辑原文与时间轴，改完点「保存修改」</CardDescription>
          </CardHeader>
          <CardContent className="h-[calc(100%-4rem)]">
            <CueTable
              cues={workspace?.cues ?? []}
              editable={{ start: true, end: true, text: true, translation: false }}
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
