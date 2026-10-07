import { useCallback, useState } from "react";
import { api } from "@/lib/api";
import { useToast } from "@/components/Toast";

/**
 * 各页读取 / 保存全局配置的通用 hook。
 * - load(): 从 config.yaml 读取，返回 values
 * - save(updates): 把 { dottedKey: value } 写回 config.yaml，结果用 toast 提示
 * 切换页面重新 mount 时调用 load 即为「实时读取配置」。
 */
export function useConfig() {
  const { toast } = useToast();
  const [saving, setSaving] = useState(false);

  const load = useCallback(async (): Promise<Record<string, unknown>> => {
    try {
      const cfg = await api.config();
      return cfg.values as Record<string, unknown>;
    } catch {
      return {};
    }
  }, []);

  const save = useCallback(
    async (updates: Record<string, unknown>) => {
      setSaving(true);
      try {
        await api.saveConfig(updates);
        toast("设置已保存", "success");
        return true;
      } catch (e) {
        toast(e instanceof Error ? e.message : "保存失败", "error");
        return false;
      } finally {
        setSaving(false);
      }
    },
    [toast],
  );

  return { load, save, saving };
}
