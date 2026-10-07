import { useRef, useState } from "react";
import { UploadCloud, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * 拖拽上传区：支持拖入文件或点击选择，上传到 /api/upload 后回调落盘路径。
 */
export function DragDrop({
  onUpload,
  accept,
  label,
  hint,
  compact = false,
}: {
  onUpload: (path: string, name: string) => void;
  accept?: string;
  label: string;
  hint?: string;
  compact?: boolean;
}) {
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setUploading(true);
    setError("");
    try {
      const fd = new FormData();
      fd.append("file", files[0]);
      const res = await fetch("/api/upload", { method: "POST", body: fd });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          detail = body.detail || detail;
        } catch {
          /* ignore */
        }
        throw new Error(detail);
      }
      const data = (await res.json()) as { path: string; name: string };
      onUpload(data.path, data.name);
    } catch (e) {
      setError(e instanceof Error ? e.message : "上传失败");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="space-y-1.5">
      <div
        role="button"
        tabIndex={0}
        aria-label={label}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          handleFiles(e.dataTransfer.files);
        }}
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-md border-2 border-dashed text-center transition-colors duration-150",
          compact ? "px-3 py-2" : "px-4 py-5",
          dragging
            ? "border-primary bg-primary/5"
            : "border-border hover:border-primary/50 hover:bg-muted/50",
        )}
      >
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
        {uploading ? (
          <Loader2 className="h-5 w-5 animate-spin text-primary" aria-hidden />
        ) : (
          <UploadCloud className="h-5 w-5 text-muted-foreground" aria-hidden />
        )}
        <span className="text-sm font-medium">{uploading ? "上传中…" : label}</span>
        {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
      </div>
      {error && <div className="text-xs text-destructive">{error}</div>}
    </div>
  );
}
