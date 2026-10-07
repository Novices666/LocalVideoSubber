import { useEffect, useState } from "react";
import { Play, Square, Film, FileDown, MonitorPlay, Save, FileUp, Download } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ProgressPanel } from "@/components/ProgressPanel";
import { DragDrop } from "@/components/DragDrop";
import { useToast } from "@/components/Toast";
import { useJob } from "@/hooks/useJob";
import { useConfig } from "@/hooks/useConfig";
import { useWorkspace } from "@/components/WorkspaceContext";
import { api, type StyleProfile } from "@/lib/api";
import { getNested } from "@/lib/utils";

const ENCODERS = ["auto", "h264_nvenc", "hevc_nvenc", "libx264", "libx265", "h264_qsv", "h264_amf"];

export function RenderPage() {
  const { workspace, refresh } = useWorkspace();
  const { state, start, cancel } = useJob();
  const { toast } = useToast();
  const { load, save, saving } = useConfig();

  const [styles, setStyles] = useState<StyleProfile[]>([]);
  const [mode, setMode] = useState("burn");
  const [styleProfile, setStyleProfile] = useState("标准样式");
  const [displayMode, setDisplayMode] = useState("both");
  const [encoder, setEncoder] = useState("auto");
  const [crf, setCrf] = useState(20);
  const [container, setContainer] = useState("mkv");
  const [video, setVideo] = useState("");
  const [importPath, setImportPath] = useState("");

  useEffect(() => {
    api.styles().then((s) => {
      setStyles(s.styles);
      if (s.styles.length > 0 && !styleProfile) setStyleProfile(s.styles[0].name);
    });
    api.workspace().then((ws) => {
      if (ws.video) setVideo(ws.video);
    });
    load().then((v) => {
      setEncoder(String(getNested(v, "render.burn_encoder", "auto")));
      setCrf(Number(getNested(v, "render.burn_crf", 20)));
      setContainer(String(getNested(v, "render.soft_container", "mkv")));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const outputVideo =
    state.done?.result?.output &&
    /\.(mp4|mkv|mov|webm|m4v)$/i.test(String(state.done.result.output));

  function submit() {
    start(() =>
      api.render({
        mode,
        display_mode: displayMode,
        encoder,
        crf,
        container,
        style_profile: styleProfile,
        video: video || undefined,
      }),
    );
  }

  async function savePage() {
    await save({
      "render.burn_encoder": encoder,
      "render.burn_crf": crf,
      "render.soft_container": container,
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
            <CardTitle>编码与视频</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <DragDrop
              label="拖入视频文件"
              hint="上传后自动填入下方路径（软封装/硬烧录需要）"
              accept=".mp4,.mkv,.avi,.mov,.flv,.wmv,.webm,.m4v"
              compact
              onUpload={(path) => setVideo(path)}
            />
            <div className="space-y-1.5">
              <Label>原始视频（软封装/硬烧录需要）</Label>
              <Input
                value={video}
                onChange={(e) => setVideo(e.target.value)}
                placeholder="留空则用工作区关联的视频"
              />
            </div>
            {mode !== "export" && (
              <>
                <div className="grid grid-cols-2 gap-2">
                  <div className="space-y-1.5">
                    <Label>编码器</Label>
                    <Select value={encoder} onValueChange={setEncoder}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {ENCODERS.map((e) => (
                          <SelectItem key={e} value={e}>
                            {e}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  {mode === "soft" ? (
                    <div className="space-y-1.5">
                      <Label>容器</Label>
                      <Select value={container} onValueChange={setContainer}>
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="mkv">mkv（推荐，支持样式）</SelectItem>
                          <SelectItem value="mp4">mp4</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                  ) : (
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between">
                        <Label>质量参数（越小越好）</Label>
                        <span className="tabular-nums text-xs text-muted-foreground">{crf}</span>
                      </div>
                      <Slider value={[crf]} min={10} max={35} step={1} onValueChange={(v) => setCrf(v[0])} />
                    </div>
                  )}
                </div>
              </>
            )}
          </CardContent>
        </Card>


        <Card>
          <CardHeader className="pb-2">
            <CardTitle>输出方式</CardTitle>
            <CardDescription>三种产出对应不同用途</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {[
              { v: "export", label: "导出字幕文件", desc: "srt / vtt / ass，用于上传或外挂", icon: FileDown },
              { v: "soft", label: "软字幕封装", desc: "内挂字幕轨，不重编码，秒级", icon: Film },
              { v: "burn", label: "硬字幕烧录", desc: "字幕画进画面，显卡加速", icon: MonitorPlay },
            ].map(({ v, label, desc, icon: Icon }) => (
              <button
                key={v}
                onClick={() => setMode(v)}
                className={`flex w-full items-start gap-3 rounded-md border p-3 text-left transition-colors duration-150 cursor-pointer ${
                  mode === v
                    ? "border-primary bg-primary/5"
                    : "border-border hover:border-primary/40"
                }`}
              >
                <Icon className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
                <span>
                  <span className="block text-sm font-medium">{label}</span>
                  <span className="block text-xs text-muted-foreground">{desc}</span>
                </span>
              </button>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle>字幕样式</CardTitle>
            <CardDescription>从字幕设置页保存的规范文件中选择</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="space-y-1.5">
              <Label>样式规范</Label>
              <Select value={styleProfile} onValueChange={setStyleProfile}>
                <SelectTrigger>
                  <SelectValue placeholder="选择样式" />
                </SelectTrigger>
                <SelectContent>
                  {styles.map((s) => (
                    <SelectItem key={s.name} value={s.name}>
                      {s.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>字幕内容</Label>
              <Select value={displayMode} onValueChange={setDisplayMode}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="both">双语（原文+译文）</SelectItem>
                  <SelectItem value="target">仅译文</SelectItem>
                  <SelectItem value="source">仅原文</SelectItem>
                </SelectContent>
              </Select>
            </div>
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
                开始渲染
              </Button>
            )}
            <Button variant="outline" onClick={savePage} disabled={saving}>
              <Save />
              保存设置
            </Button>
          </div>
        </div>
      </div>

      {/* 右栏：进度 + 预览 */}
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
            <CardTitle>结果预览</CardTitle>
            <CardDescription>
              {workspace?.cue_count ? `当前字幕 ${workspace.cue_count} 条` : "无字幕"}
            </CardDescription>
          </CardHeader>
          <CardContent className="h-[calc(100%-4rem)]">
            {outputVideo && state.done?.result?.output ? (
              <video
                key={String(state.done.result.output)}
                src={`/api/media/file?path=${encodeURIComponent(String(state.done.result.output))}`}
                controls
                className="h-full w-full rounded-md border border-border bg-black"
              />
            ) : state.done?.result?.mode === "export" ? (
              <div className="scroll-area h-full space-y-2 overflow-y-auto">
                <div className="text-sm">导出完成，共 {Array.isArray(state.done.result.files) ? state.done.result.files.length : 0} 个文件：</div>
                {Array.isArray(state.done.result.files) &&
                  state.done.result.files.map((f) => (
                    <a
                      key={String(f)}
                      href={`/api/media/file?path=${encodeURIComponent(String(f))}`}
                      download
                      className="flex items-center justify-between rounded-md border border-border bg-muted/40 px-3 py-2 text-sm transition-colors hover:bg-muted cursor-pointer"
                    >
                      <span className="break-anywhere">{String(f).split(/[\\/]/).pop()}</span>
                      <Download className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                    </a>
                  ))}
                <div className="text-xs text-muted-foreground">
                  点击文件名下载。文件也保存在 output 目录，可用播放器外挂加载。
                </div>
              </div>
            ) : state.done?.result?.output ? (
              <div className="space-y-2">
                <div className="text-sm">
                  产物：<span className="break-anywhere font-medium">{String(state.done.result.output)}</span>
                </div>
                <div className="text-xs text-muted-foreground">
                  软封装与硬烧录产物保存在 output 目录，播放器可直接打开预览。
                </div>
              </div>
            ) : (
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                渲染完成后，这里显示产物信息。
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
