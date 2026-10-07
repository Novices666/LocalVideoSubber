import { Outlet, NavLink, Link } from "react-router-dom";
import {
  Home,
  Mic,
  Languages,
  Clapperboard,
  Type,
  Settings,
  FileVideo2,
  Captions,
  PackageCheck,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { WorkspaceProvider, useWorkspace } from "@/components/WorkspaceContext";

const NAV_ITEMS = [
  { to: "/", label: "主页", icon: Home },
  { to: "/transcribe", label: "转录", icon: Mic },
  { to: "/translate", label: "翻译", icon: Languages },
  { to: "/render", label: "渲染", icon: Clapperboard },
  { to: "/subtitle-settings", label: "字幕设置", icon: Type },
  { to: "/settings", label: "设置", icon: Settings },
];

function Sidebar() {
  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-border bg-card">
      <div className="flex h-14 items-center gap-2 border-b border-border px-4">
        <FileVideo2 className="h-5 w-5 text-primary" aria-hidden />
        <Link to="/" className="font-display text-sm font-semibold tracking-tight">
          LocalVideoSubber
        </Link>
      </div>
      <nav className="flex-1 space-y-1 p-2" aria-label="主导航">
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors duration-150",
                isActive
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )
            }
          >
            <Icon className="h-4 w-4" aria-hidden />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-border p-3 text-xs text-muted-foreground">
        本地视频字幕工作台
      </div>
    </aside>
  );
}

function WorkspaceBar() {
  const { workspace } = useWorkspace();
  if (!workspace) return null;

  const items: { label: string; value: string }[] = [
    {
      label: "当前字幕",
      value:
        workspace.cue_count > 0
          ? `${workspace.cue_count} 条${workspace.translated ? ` · 已译 ${workspace.translated}` : ""}`
          : "无",
    },
    {
      label: "关联视频",
      value: workspace.video_stem || "无",
    },
    {
      label: "最近产物",
      value: workspace.last_output ? workspace.last_output.split(/[\\/]/).pop() || "" : "无",
    },
  ];

  return (
    <header className="flex h-11 shrink-0 items-center gap-6 border-b border-border bg-card px-4 text-xs">
      {items.map((it) => (
        <div key={it.label} className="flex items-baseline gap-1.5">
          <span className="text-muted-foreground">{it.label}</span>
          <span className="max-w-64 truncate font-medium">{it.value}</span>
        </div>
      ))}
      <div className="ml-auto flex items-center gap-3 text-muted-foreground">
        <span className="flex items-center gap-1">
          <Captions className="h-3.5 w-3.5" aria-hidden />
          字幕
        </span>
        <span className="flex items-center gap-1">
          <PackageCheck className="h-3.5 w-3.5" aria-hidden />
          本地运行
        </span>
      </div>
    </header>
  );
}

export function AppLayout() {
  return (
    <WorkspaceProvider>
      <div className="flex h-screen overflow-hidden">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <WorkspaceBar />
          <main className="min-h-0 flex-1 overflow-hidden">
            <Outlet />
          </main>
        </div>
      </div>
    </WorkspaceProvider>
  );
}
