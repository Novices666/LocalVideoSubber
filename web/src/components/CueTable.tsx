import { useState } from "react";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Cue } from "@/lib/api";

export interface CueEditable {
  start?: boolean;
  end?: boolean;
  text?: boolean;
  translation?: boolean;
}

export function CueTable({
  cues,
  editable = { start: true, end: true, text: true, translation: true },
  onSave,
  className,
}: {
  cues: Cue[];
  editable?: CueEditable;
  onSave?: (cues: Cue[]) => Promise<void> | void;
  className?: string;
}) {
  const [draft, setDraft] = useState<Cue[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState("");

  const rows = draft ?? cues;
  const dirty = draft !== null;

  function update(index: number, field: keyof Cue, value: string | number) {
    setDraft((prev) => {
      const base = prev ?? cues.map((c) => ({ ...c }));
      return base.map((c, i) => (i === index ? { ...c, [field]: value } : c));
    });
    setSavedMsg("");
  }

  function reset() {
    setDraft(null);
    setSavedMsg("");
  }

  async function save() {
    if (!onSave || !draft) return;
    setSaving(true);
    try {
      // 数值字段归一
      const normalized = draft.map((c) => ({
        ...c,
        start: Number(c.start),
        end: Number(c.end),
      }));
      await onSave(normalized);
      setDraft(null);
      setSavedMsg("已保存");
    } catch (e) {
      setSavedMsg(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  if (cues.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        暂无字幕。完成转录或导入字幕文件后，这里会显示条目。
      </div>
    );
  }

  const canEditAny =
    editable.start || editable.end || editable.text || editable.translation;

  return (
    <div className={cn("flex h-full flex-col", className)}>
      <div className="scroll-area min-h-0 flex-1 overflow-auto rounded-md border border-border">
        <Table>
          <TableHeader className="sticky top-0 z-10 bg-muted">
            <TableRow>
              <TableHead className="w-12 text-right">#</TableHead>
              <TableHead className="w-24">开始</TableHead>
              <TableHead className="w-24">结束</TableHead>
              <TableHead className="w-[38%]">原文</TableHead>
              <TableHead className="w-[38%]">译文</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((cue, i) => (
              <TableRow key={i}>
                <TableCell className="tabular-nums text-right text-muted-foreground">
                  {cue.index}
                </TableCell>
                <TableCell>
                  {editable.start ? (
                    <Input
                      value={cue.start}
                      onChange={(e) => update(i, "start", e.target.value)}
                      className="tabular-nums h-7 px-2 text-xs"
                    />
                  ) : (
                    <span className="tabular-nums">{cue.start.toFixed(2)}</span>
                  )}
                </TableCell>
                <TableCell>
                  {editable.end ? (
                    <Input
                      value={cue.end}
                      onChange={(e) => update(i, "end", e.target.value)}
                      className="tabular-nums h-7 px-2 text-xs"
                    />
                  ) : (
                    <span className="tabular-nums">{cue.end.toFixed(2)}</span>
                  )}
                </TableCell>
                <TableCell>
                  {editable.text ? (
                    <Input
                      value={cue.text}
                      onChange={(e) => update(i, "text", e.target.value)}
                      className="h-7 px-2 text-xs"
                    />
                  ) : (
                    <span className="break-anywhere">{cue.text}</span>
                  )}
                </TableCell>
                <TableCell>
                  {editable.translation ? (
                    <Input
                      value={cue.translation}
                      onChange={(e) => update(i, "translation", e.target.value)}
                      placeholder="（未翻译）"
                      className="h-7 px-2 text-xs"
                    />
                  ) : (
                    <span className="break-anywhere text-muted-foreground">
                      {cue.translation || "—"}
                    </span>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {canEditAny && (
        <div className="mt-2 flex items-center gap-2">
          {dirty ? (
            <>
              <Button size="sm" onClick={save} disabled={saving}>
                {saving ? "保存中…" : "保存修改"}
              </Button>
              <Button size="sm" variant="ghost" onClick={reset}>
                放弃修改
              </Button>
            </>
          ) : (
            <span className="text-xs text-muted-foreground">
              点击表格单元格可编辑时间轴与文本
            </span>
          )}
          {savedMsg && <span className="text-xs text-accent">{savedMsg}</span>}
        </div>
      )}
    </div>
  );
}
