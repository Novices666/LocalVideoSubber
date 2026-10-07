import { useId } from "react";
import { RefreshCw, Loader2 } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * 模型选择器：输入框（可自由输入）+ datalist 下拉建议 + 刷新按钮。
 * 既支持从服务端/本地拉取的模型下拉选择，也支持手动填写任意模型 ID。
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
  options: string[];
  onRefresh: () => void | Promise<void>;
  refreshing?: boolean;
  placeholder?: string;
  className?: string;
}) {
  const listId = useId();

  return (
    <div className={cn("flex gap-1.5", className)}>
      <div className="relative min-w-0 flex-1">
        <Input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          list={listId}
          className="pr-1"
        />
        <datalist id={listId}>
          {options.map((opt) => (
            <option key={opt} value={opt} />
          ))}
        </datalist>
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
