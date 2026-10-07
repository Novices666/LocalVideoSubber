import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// ASS 颜色是 &HAABBGGRR（BGR 顺序），HTML color input 是 #RRGGBB。
export function assToHex(ass: string): string {
  const m = ass.match(/&H([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})/);
  if (!m) return "#ffffff";
  const [, , bb, gg, rr] = m;
  return `#${rr}${gg}${bb}`;
}

export function hexToAss(hex: string): string {
  const h = hex.replace("#", "");
  if (h.length < 6) return "&H00FFFFFF";
  const rr = h.slice(0, 2);
  const gg = h.slice(2, 4);
  const bb = h.slice(4, 6);
  return `&H00${bb}${gg}${rr}`;
}

// 从嵌套配置对象按点号路径取值
export function getNested(
  obj: Record<string, unknown>,
  dotted: string,
  fallback: unknown,
): unknown {
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

// 把 {原文: 译文} 术语表转成每行一条的文本
export function glossaryToText(glossary: Record<string, string> | undefined): string {
  if (!glossary) return "";
  return Object.entries(glossary)
    .map(([k, v]) => `${k}: ${v}`)
    .join("\n");
}
