import { useCallback, useEffect, useState } from "react";
import {
  Folder,
  FolderOpen,
  FileText,
  FileVideo,
  FileAudio,
  Image as ImageIcon,
  Trash2,
  RefreshCw,
  Sparkles,
  Download,
  File as FileIcon,
} from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/Toast";
import { api, type FileEntry } from "@/lib/api";
import { cn } from "@/lib/utils";

const MEDIA = /\.(mp4|mkv|mov|webm|m4v)$/i;
const AUDIO = /\.(mp3|wav|m4a|aac|flac|ogg)$/i;
const IMAGE = /\.(png|jpg|jpeg|gif|webp|bmp)$/i;
const TEXT = /\.(srt|vtt|ass|ssa|txt|json|yaml|yml|md|log)$/i;

function fmtSize(bytes: number | undefined): string {
  if (bytes === undefined) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function fileIcon(name: string, type: "dir" | "file") {
  if (type === "dir") return null;
  if (MEDIA.test(name)) return <FileVideo className="h-4 w-4 shrink-0 text-primary" aria-hidden />;
  if (AUDIO.test(name)) return <FileAudio className="h-4 w-4 shrink-0 text-primary" aria-hidden />;
  if (IMAGE.test(name)) return <ImageIcon className="h-4 w-4 shrink-0 text-primary" aria-hidden />;
  if (TEXT.test(name)) return <FileText className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />;
  return <FileIcon className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />;
}

export function FilesPage() {
  const { toast } = useToast();
  const [root, setRoot] = useState("");
  const [children, setChildren] = useState<Map<string, FileEntry[]>>(new Map());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<FileEntry | null>(null);
  const [cleaning, setCleaning] = useState(false);

  const loadFolder = useCallback(
    async (path: string) => {
      try {
        const r = await api.filesTree(path);
        setRoot((prev) => prev || r.folder);
        setChildren((prev) => new Map(prev).set(r.folder, r.entries));
        if (!r.folder.endsWith(path.split(/[\\/]/).pop() || "")) {
          // 返回的 folder 是解析后的路径，用它做 key
          setChildren((prev) => {
            const m = new Map(prev);
            m.delete(path);
            m.set(r.folder, r.entries);
            return m;
          });
        }
      } catch (e) {
        toast(e instanceof Error ? e.message : "加载目录失败", "error");
      }
    },
    [toast],
  );

  useEffect(() => {
    api
      .config()
      .then((c) => loadFolder(c.output_dir))
      .catch(() => toast("加载工作目录失败", "error"));
  }, [loadFolder, toast]);

  function toggleDir(entry: FileEntry) {
    if (expanded.has(entry.path)) {
      setExpanded((prev) => {
        const s = new Set(prev);
        s.delete(entry.path);
        return s;
      });
    } else {
      setExpanded((prev) => new Set(prev).add(entry.path));
      if (!children.has(entry.path)) loadFolder(entry.path);
    }
  }

  function selectEntry(entry: FileEntry) {
    setSelected(entry);
  }

  async function removeEntry(entry: FileEntry) {
    if (!window.confirm(`删除 ${entry.name}${entry.type === "dir" ? "（含全部内容）" : ""}？此操作不可撤销。`)) {
      return;
    }
    try {
      await api.filesDelete(entry.path);
      toast(`已删除 ${entry.name}`, "success");
      setSelected(null);
      // 刷新父目录
      const parent = entry.path.split(/[\\/]/).slice(0, -1).join("\\");
      const key = [...children.keys()].find((k) => k === parent || entry.path.startsWith(k));
      if (key) loadFolder(key);
      if (parent) loadFolder(parent);
    } catch (e) {
      toast(e instanceof Error ? e.message : "删除失败", "error");
    }
  }

  async function cleanup() {
    if (
      !window.confirm(
        "清理所有项目的中间产物（transcribe/translate/burn 的 J 目录）？\n上传的原视频和成品会被保留。",
      )
    ) {
      return;
    }
    setCleaning(true);
    try {
      const r = await api.filesCleanup();
      toast(`清理完成：删除 ${r.removed} 个目录，释放 ${fmtSize(r.freed)}`, "success");
      // 重新加载根
      const rootPath = root;
      setChildren(new Map());
      setExpanded(new Set());
      if (rootPath) loadFolder(rootPath);
    } catch (e) {
      toast(e instanceof Error ? e.message : "清理失败", "error");
    } finally {
      setCleaning(false);
    }
  }

  function renderNodes(entries: FileEntry[], depth: number) {
    return entries.map((entry) => (
      <div key={entry.path}>
        <div
          onClick={() => (entry.type === "dir" ? toggleDir(entry) : selectEntry(entry))}
          className={cn(
            "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors cursor-pointer",
            selected?.path === entry.path ? "bg-primary/10" : "hover:bg-muted/50",
          )}
          style={{ paddingLeft: `${8 + depth * 16}px` }}
        >
          {entry.type === "dir" ? (
            expanded.has(entry.path) ? (
              <FolderOpen className="h-4 w-4 shrink-0 text-primary" aria-hidden />
            ) : (
              <Folder className="h-4 w-4 shrink-0 text-primary" aria-hidden />
            )
          ) : (
            fileIcon(entry.name, "file")
          )}
          <span className="min-w-0 flex-1 truncate">{entry.name}</span>
          {entry.type === "file" && entry.size !== undefined && (
            <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
              {fmtSize(entry.size)}
            </span>
          )}
          <button
            onClick={(e) => {
              e.stopPropagation();
              removeEntry(entry);
            }}
            className="shrink-0 rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
            aria-label={`删除 ${entry.name}`}
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden />
          </button>
        </div>
        {entry.type === "dir" && expanded.has(entry.path) && children.has(entry.path) && (
          <div>{renderNodes(children.get(entry.path)!, depth + 1)}</div>
        )}
      </div>
    ));
  }

  const rootEntries = children.get(root) ?? [];

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-6 py-3">
        <div>
          <h1 className="font-display text-xl font-semibold">文件</h1>
          <p className="text-xs text-muted-foreground">
            工作目录 {root || "…"} · 按视频分组 · upload/transcribe/translate/burn/output
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => loadFolder(root)}>
            <RefreshCw />
            刷新
          </Button>
          <Button size="sm" variant="outline" onClick={cleanup} disabled={cleaning || !root}>
            <Sparkles />
            {cleaning ? "清理中…" : "一键清理中间产物"}
          </Button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 gap-4 p-4">
        {/* 左：目录树 */}
        <Card className="w-[380px] shrink-0">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">目录</CardTitle>
          </CardHeader>
          <CardContent className="h-[calc(100%-3.5rem)] overflow-y-auto p-2">
            {rootEntries.length === 0 ? (
              <div className="py-8 text-center text-sm text-muted-foreground">
                工作目录为空
              </div>
            ) : (
              renderNodes(rootEntries, 0)
            )}
          </CardContent>
        </Card>

        {/* 右：预览 */}
        <Card className="min-w-0 flex-1">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">预览</CardTitle>
            <CardDescription className="break-anywhere">
              {selected ? selected.path : "选中左侧文件查看内容"}
            </CardDescription>
          </CardHeader>
          <CardContent className="h-[calc(100%-4rem)] overflow-hidden p-2">
            {!selected ? (
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                选中文件后在此预览
              </div>
            ) : selected.type === "dir" ? (
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                这是文件夹，展开查看内容
              </div>
            ) : MEDIA.test(selected.name) ? (
              <video
                key={selected.path}
                src={`/api/media/file?path=${encodeURIComponent(selected.path)}`}
                controls
                className="h-full w-full rounded-md border border-border bg-black"
              />
            ) : AUDIO.test(selected.name) ? (
              <audio
                key={selected.path}
                src={`/api/media/file?path=${encodeURIComponent(selected.path)}`}
                controls
                className="w-full"
              />
            ) : IMAGE.test(selected.name) ? (
              <img
                key={selected.path}
                src={`/api/media/file?path=${encodeURIComponent(selected.path)}`}
                alt={selected.name}
                className="max-h-full max-w-full rounded-md border border-border object-contain"
              />
            ) : TEXT.test(selected.name) ? (
              <iframe
                key={selected.path}
                src={`/api/media/file?path=${encodeURIComponent(selected.path)}`}
                title={selected.name}
                className="h-full w-full rounded-md border border-border bg-white"
              />
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
                <FileIcon className="h-8 w-8" aria-hidden />
                <span>此类型暂不支持预览</span>
                <a
                  href={`/api/media/file?path=${encodeURIComponent(selected.path)}`}
                  download
                  className="flex items-center gap-1 text-primary hover:underline"
                >
                  <Download className="h-4 w-4" aria-hidden />
                  下载文件
                </a>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
