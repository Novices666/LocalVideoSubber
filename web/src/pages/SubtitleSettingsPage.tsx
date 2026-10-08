import { useEffect, useRef, useState } from "react";
import { Plus, Trash2, Save, RefreshCw } from "lucide-react";
import { useToast } from "@/components/Toast";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api, type StyleProfile } from "@/lib/api";
import { assToHex, hexToAss, cn } from "@/lib/utils";

const FONTS = ["Microsoft YaHei", "SimHei", "SimSun", "DengXian", "Arial", "Noto Sans CJK SC"];

const EMPTY: StyleProfile = {
  name: "",
  font_name: "Microsoft YaHei",
  font_size: 42,
  source_font_size: 32,
  target_font_size: 42,
  primary_color: "&H00FFFFFF",
  secondary_color: "&H000000FF",
  outline_color: "&H00000000",
  back_color: "&H80000000",
  outline: 2,
  source_outline: 2,
  target_outline: 2,
  shadow: 1,
  margin_v: 40,
  source_margin_v: null,
  target_margin_v: null,
  order: "target_top",
  orientation: "landscape",
};

export function SubtitleSettingsPage() {
  const { toast } = useToast();
  const [styles, setStyles] = useState<StyleProfile[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [draft, setDraft] = useState<StyleProfile>({ ...EMPTY });
  const [refreshing, setRefreshing] = useState(false);
  const [sysFonts, setSysFonts] = useState<string[]>([]);

  async function load() {
    const s = await api.styles();
    setStyles(s.styles);
    return s.styles;
  }

  useEffect(() => {
    load().then((s) => {
      if (s.length > 0) {
        setSelected(s[0].name);
        setDraft({ ...s[0] });
      }
    });
    api.fonts().then((r) => setSysFonts(r.fonts)).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function refresh() {
    setRefreshing(true);
    try {
      const s = await load();
      // 当前选中项仍存在则保留，否则回退到第一个
      setSelected((cur) => {
        if (cur && s.some((x) => x.name === cur)) return cur;
        return s.length > 0 ? s[0].name : "";
      });
      const cur = s.find((x) => x.name === selected) ?? s[0];
      if (cur) setDraft({ ...cur });
    } finally {
      setRefreshing(false);
    }
  }

  function select(name: string) {
    setSelected(name);
    const found = styles.find((s) => s.name === name);
    if (found) setDraft({ ...found });
  }

  function createNew() {
    setSelected("");
    setDraft({ ...EMPTY, name: "新样式" });
  }

  async function save() {
    if (!draft.name.trim()) {
      toast("请填写样式名", "error");
      return;
    }
    try {
      const { style } = await api.saveStyle(draft);
      toast(`已保存「${style.name}」`, "success");
      await load();
      setSelected(style.name);
    } catch (e) {
      toast(e instanceof Error ? e.message : "保存失败", "error");
    }
  }

  async function remove() {
    if (!selected) return;
    try {
      await api.deleteStyle(selected);
      toast(`已删除「${selected}」`, "success");
      const s = await load();
      const first = s.length > 0 ? s[0] : null;
      setSelected(first ? first.name : "");
      setDraft(first ? { ...first } : { ...EMPTY });
    } catch (e) {
      toast(e instanceof Error ? e.message : "删除失败", "error");
    }
  }

  function set<K extends keyof StyleProfile>(key: K, value: StyleProfile[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  const targetOnTop = draft.order === "target_top";
  const isPortrait = (draft.orientation ?? "landscape") === "portrait";
  const refH = isPortrait ? 1920 : 1080;

  // 预览画布自适应：横屏取宽度最大、竖屏取高度最大，按比例渲染，不超出容器
  const previewRef = useRef<HTMLDivElement>(null);
  const [avail, setAvail] = useState({ w: 600, h: 400 });
  useEffect(() => {
    const el = previewRef.current;
    if (!el) return;
    const measure = () =>
      setAvail({
        w: Math.max(280, el.clientWidth),
        h: Math.max(240, el.clientHeight),
      });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // 横屏：宽 = min(可用宽, 可用高 * 16/9)；竖屏：高 = min(可用高, 可用宽 * 16/9)
  const canvasW = isPortrait
    ? Math.min(avail.w, avail.h * (9 / 16))
    : Math.min(avail.w, avail.h * (16 / 9));
  const canvasH = isPortrait ? canvasW * (16 / 9) : canvasW * (9 / 16);
  const scale = canvasH / refH;
  const tgtSize = draft.target_font_size * scale * 2;
  const srcSize = draft.source_font_size * scale * 2;
  const bottom = ((draft.target_margin_v ?? draft.margin_v) / refH) * canvasH;

  return (
    <div className="flex h-full">
      {/* 左栏：样式列表 */}
      <div className="flex w-72 shrink-0 flex-col border-r border-border p-4">
        <div className="mb-3 flex items-center justify-between">
          <h1 className="font-display text-base font-semibold">样式规范</h1>
          <div className="flex gap-1.5">
            <Button size="sm" variant="ghost" onClick={refresh} disabled={refreshing} title="刷新列表" aria-label="刷新样式列表">
              <RefreshCw className={refreshing ? "animate-spin" : ""} />
            </Button>
            <Button size="sm" variant="outline" onClick={createNew}>
              <Plus />
              新建
            </Button>
          </div>
        </div>
        <div className="scroll-area min-h-0 flex-1 space-y-2 overflow-y-auto">
          {[
            { key: "landscape", label: "横屏" },
            { key: "portrait", label: "竖屏" },
          ].map(({ key, label }) => {
            const group = styles.filter((s) => (s.orientation ?? "landscape") === key);
            if (group.length === 0) return null;
            return (
              <div key={key}>
                <div className="px-3 pb-1 text-xs font-medium text-muted-foreground">{label}</div>
                <div className="space-y-1">
                  {group.map((s) => (
                    <button
                      key={s.name}
                      onClick={() => select(s.name)}
                      className={cn(
                        "flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm transition-colors cursor-pointer",
                        selected === s.name
                          ? "bg-primary/10 text-primary"
                          : "hover:bg-muted",
                      )}
                    >
                      <span className="truncate">{s.name}</span>
                      <span className="tabular-nums text-xs text-muted-foreground">{s.font_size}px</span>
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* 右栏：编辑 + 预览 */}
      <div className="flex min-w-0 flex-1 gap-4 overflow-hidden p-4">
        <div className="w-[400px] shrink-0 space-y-3">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle>基本</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <div className="space-y-1">
                <Label>样式名</Label>
                <Input value={draft.name} onChange={(e) => set("name", e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label>字体</Label>
                <Select value={draft.font_name} onValueChange={(v) => set("font_name", v)}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="max-h-72">
                    <SelectGroup>
                      <SelectLabel>推荐</SelectLabel>
                      {FONTS.map((f) => (
                        <SelectItem key={f} value={f}>
                          {f}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                    <SelectGroup>
                      <SelectLabel>系统字体</SelectLabel>
                      {sysFonts
                        .filter((f) => !FONTS.includes(f))
                        .map((f) => (
                          <SelectItem key={f} value={f}>
                            {f}
                          </SelectItem>
                        ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>双语顺序</Label>
                <Select value={draft.order} onValueChange={(v) => set("order", v as "target_top" | "source_top")}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="target_top">译文在上</SelectItem>
                    <SelectItem value="source_top">原文在上</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>画布方向</Label>
                <Select
                  value={draft.orientation ?? "landscape"}
                  onValueChange={(v) => set("orientation", v as "landscape" | "portrait")}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="landscape">横屏（16:9，电影/桌面）</SelectItem>
                    <SelectItem value="portrait">竖屏（9:16，短视频/手机）</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle>字号与边距</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-x-3 gap-y-2">
              {[
                {
                  key: "target_font_size",
                  label: "译文字号",
                  fallback: undefined as number | undefined,
                  min: 12,
                  max: 80,
                  step: 1,
                },
                {
                  key: "source_font_size",
                  label: "原文字号",
                  fallback: undefined as number | undefined,
                  min: 12,
                  max: 80,
                  step: 1,
                },
                {
                  key: "target_margin_v",
                  label: "译文距底部边距",
                  fallback: draft.margin_v,
                  min: 0,
                  max: 200,
                  step: 5,
                },
                {
                  key: "source_margin_v",
                  label: "原文距底部边距",
                  fallback: draft.margin_v,
                  min: 0,
                  max: 200,
                  step: 5,
                },
                {
                  key: "target_outline",
                  label: "译文描边宽度",
                  fallback: draft.outline,
                  min: 0,
                  max: 5,
                  step: 1,
                },
                {
                  key: "source_outline",
                  label: "原文描边宽度",
                  fallback: draft.outline,
                  min: 0,
                  max: 5,
                  step: 1,
                },
              ].map(({ key, label, fallback, min, max, step }) => {
                const raw = draft[key as keyof StyleProfile] as number | null | undefined;
                const val = raw ?? fallback ?? 0;
                return (
                  <div key={key} className="space-y-1">
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-muted-foreground">{label}</span>
                      <span className="tabular-nums text-xs">{val}</span>
                    </div>
                    <Slider
                      value={[val]}
                      min={min}
                      max={max}
                      step={step}
                      onValueChange={(v) => set(key as keyof StyleProfile, v[0])}
                    />
                  </div>
                );
              })}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle>颜色</CardTitle>
              <CardDescription>ASS 颜色格式（&H 前缀 BGR）</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {[
                { key: "primary_color", label: "文字颜色" },
                { key: "outline_color", label: "描边颜色" },
                { key: "back_color", label: "背景阴影颜色" },
              ].map(({ key, label }) => (
                <div key={key} className="flex items-center justify-between">
                  <Label>{label}</Label>
                  <div className="flex items-center gap-2">
                    <input
                      type="color"
                      value={assToHex(draft[key as keyof StyleProfile] as string)}
                      onChange={(e) =>
                        set(key as keyof StyleProfile, hexToAss(e.target.value))
                      }
                      className="h-8 w-10 cursor-pointer rounded border border-border bg-card"
                    />
                    <span className="tabular-nums text-xs text-muted-foreground">
                      {draft[key as keyof StyleProfile] as string}
                    </span>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>

          <div className="flex gap-2 pb-4">
            <Button className="flex-1" onClick={save}>
              <Save />
              保存样式
            </Button>
            {selected && (
              <Button variant="destructive" onClick={remove} disabled={!selected}>
                <Trash2 />
                删除
              </Button>
            )}
          </div>
        </div>

        {/* 预览 */}
        <div className="min-w-0 flex-1">
          <Card className="flex h-full flex-col">
            <CardHeader className="pb-2">
              <CardTitle>预览</CardTitle>
              <CardDescription>
                {isPortrait ? "竖屏 9:16（短视频/手机）" : "横屏 16:9（电影/桌面）"} · 样式实时效果
              </CardDescription>
            </CardHeader>
            <CardContent ref={previewRef} className="flex min-h-0 flex-1 items-center justify-center overflow-hidden p-0">
              <div
                className="relative flex shrink-0 items-end justify-center overflow-hidden rounded-lg bg-slate-800"
                style={{ width: canvasW, height: canvasH }}
              >
                <div
                  className="absolute inset-x-0 text-center"
                  style={{
                    bottom: `${bottom}px`,
                    fontFamily: `"${draft.font_name}", sans-serif`,
                  }}
                >
                  {targetOnTop ? (
                    <>
                      <div
                        style={{
                          fontSize: `${tgtSize}px`,
                          color: assToHex(draft.primary_color),
                          WebkitTextStroke: `${draft.target_outline ?? draft.outline}px ${assToHex(draft.outline_color)}`,
                          textShadow: `0 ${draft.shadow}px 3px ${assToHex(draft.back_color)}`,
                          lineHeight: 1.2,
                        }}
                      >
                        这是译文显示效果
                      </div>
                      <div
                        style={{
                          fontSize: `${srcSize}px`,
                          color: assToHex(draft.primary_color),
                          WebkitTextStroke: `${draft.source_outline ?? draft.outline}px ${assToHex(draft.outline_color)}`,
                          opacity: 0.85,
                          lineHeight: 1.2,
                        }}
                      >
                        This is the source line
                      </div>
                    </>
                  ) : (
                    <>
                      <div
                        style={{
                          fontSize: `${srcSize}px`,
                          color: assToHex(draft.primary_color),
                          opacity: 0.85,
                          lineHeight: 1.2,
                        }}
                      >
                        This is the source line
                      </div>
                      <div
                        style={{
                          fontSize: `${tgtSize}px`,
                          color: assToHex(draft.primary_color),
                          WebkitTextStroke: `${draft.target_outline ?? draft.outline}px ${assToHex(draft.outline_color)}`,
                          textShadow: `0 ${draft.shadow}px 3px ${assToHex(draft.back_color)}`,
                          lineHeight: 1.2,
                        }}
                      >
                        这是译文显示效果
                      </div>
                    </>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
