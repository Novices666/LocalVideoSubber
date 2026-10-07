import { useEffect, useRef, useState } from "react";
import { RefreshCw, Loader2 } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface ModelOption {
  value: string;
  label: string;
}

/**
 * 模型选择器：可自由输入的输入框 + 下拉列表（显示 label，提交 value）+ 刷新按钮。
 * options 可为 {value,label}[] 或 string[]（此时 label=value）。
 */
export function ModelPicker({
  value,
  onChange,
  options,
  onRefresh,
  refreshing = false,
  placeholder,
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  options: (ModelOption | string)[];
  onRefresh: () => void | Promise<void>;
  refreshing?: boolean;
  placeholder?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const opts: ModelOption[] = options.map((o) =>
    typeof o === "string" ? { value: o, label: o } : o,
  );
  const current = opts.find((o) => o.value === value);
  const display = current?.label ?? value;

  // 点击外部关闭下拉
  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const filtered = opts.filter((o) => {
    const q = (value || "").toLowerCase();
    return o.label.toLowerCase().includes(q) || o.value.toLowerCase().includes(q);
  });

  return (
    <div className={cn("flex gap-1.5", className)} ref={containerRef}>
      <div className="relative min-w-0 flex-1">
        <Input
          value={display}
          onChange={(e) => {
            onChange(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          placeholder={placeholder}
        />
        {open && filtered.length > 0 && (
          <div className="absolute z-50 mt-1 max-h-64 w-full overflow-y-auto rounded-md border border-border bg-card p-1 shadow-lg">
            {filtered.map((o) => (
              <button
                key={o.value}
                type="button"
                onClick={() => {
                  onChange(o.value);
                  setOpen(false);
                }}
                className={cn(
                  "flex w-full items-center justify-between rounded-sm px-2.5 py-1.5 text-left text-sm transition-colors cursor-pointer",
                  o.value === value ? "bg-primary/10 text-primary" : "hover:bg-muted",
                )}
              >
                <span className="truncate">{o.label}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      <Button
        type="button"
        variant="outline"
        size="icon"
        onClick={() => onRefresh()}
        disabled={refreshing}
        title="刷新模型列表"
        aria-label="刷新模型列表"
      >
        {refreshing ? <Loader2 className="animate-spin" /> : <RefreshCw />}
      </Button>
    </div>
  );
}
