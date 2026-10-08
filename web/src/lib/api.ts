// 后端 API 封装。dev 时经 Vite proxy 转发到 127.0.0.1:8000。

// ---------- 类型 ----------
export interface CheckItem {
  name: string;
  ok: boolean;
  detail: string;
  level: "required" | "optional";
}

export interface CheckResult {
  ok: boolean;
  markdown: string;
  items: CheckItem[];
}

export interface ModelEntry {
  value: string;
  label: string;
}

export interface MediaInfo {
  path: string;
  duration: number;
  duration_text: string;
  has_video: boolean;
  has_audio: boolean;
  resolution: string;
  fps: number;
  video_codec: string;
  audio_codec: string;
  sample_rate: number;
  channels: number;
  container: string;
}

export interface Cue {
  index: number;
  start: number;
  end: number;
  text: string;
  translation: string;
  speaker?: string;
  style?: string;
}

export interface FileEntry {
  name: string;
  path: string;
  type: "dir" | "file";
  size?: number;
  has_children?: boolean;
}

export interface WorkspaceSnapshot {
  video: string;
  video_stem: string;
  cue_count: number;
  translated: number;
  last_output: string;
  work_dir: string;
  updated_at: number;
  stats: { count: number; duration: number; chars: number; avg_chars: number; translated: number } | null;
  cues: Cue[];
}

export interface JobSummary {
  id: string;
  kind: string;
  title: string;
  status: string;
  status_tag: string;
  stage: string;
  progress: number;
  message: string;
  elapsed: number;
  error: string;
}

export interface JobDetail extends JobSummary {
  logs: string[];
  result: Record<string, unknown>;
}

export interface StyleProfile {
  name: string;
  font_name: string;
  font_size: number;
  source_font_size: number;
  target_font_size: number;
  primary_color: string;
  secondary_color: string;
  outline_color: string;
  back_color: string;
  outline: number;
  source_outline?: number;
  target_outline?: number;
  shadow: number;
  margin_v: number;
  source_margin_v?: number | null;
  target_margin_v?: number | null;
  order: string;
  orientation?: string;
  _file?: string;
}

// ---------- 请求体 ----------
export interface TranscribeRequest {
  video: string;
  model_path?: string | null;
  engine?: string;
  language?: string;
  task?: string;
  beam_size?: number;
  device?: string;
  compute_type?: string;
  vad_filter?: boolean;
  initial_prompt?: string;
  seg_opts?: Record<string, unknown>;
}

export interface TranslateRequest {
  source_lang?: string;
  target_lang?: string;
  batch_size?: number;
  context_size?: number;
  concurrency?: number;
  base_url?: string;
  api_key?: string;
  model?: string;
  temperature?: number;
  disable_thinking?: boolean;
  glossary?: Record<string, string>;
  system_prompt?: string;
}

export interface RenderRequest {
  mode?: string;
  display_mode?: string;
  encoder?: string;
  crf?: number;
  container?: string;
  style_profile?: string;
  video?: string;
}

// ---------- 通用请求 ----------
async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
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
  return res.json() as Promise<T>;
}

// ---------- API ----------
export const api = {
  check: () => req<CheckResult>("/api/check"),
  models: () => req<{ asr: ModelEntry[]; translate: ModelEntry[] }>("/api/models"),

  probe: (path: string) =>
    req<MediaInfo>(`/api/media/probe?path=${encodeURIComponent(path)}`),

  llmTest: (base_url: string, api_key: string, model: string) =>
    req<{ ok: boolean; detail: string }>("/api/llm/test", {
      method: "POST",
      body: JSON.stringify({ base_url, api_key, model }),
    }),
  llmModels: (base_url: string, api_key: string) =>
    req<{ models: string[]; detail: string }>("/api/llm/models", {
      method: "POST",
      body: JSON.stringify({ base_url, api_key, model: "" }),
    }),

  workspace: () => req<WorkspaceSnapshot>("/api/workspace"),
  loadCues: (path: string, as_translation = false) =>
    req<{ count: number; cues: Cue[] }>("/api/workspace/cues", {
      method: "POST",
      body: JSON.stringify({ path, as_translation }),
    }),
  updateCues: (cues: Cue[]) =>
    req<{ count: number }>("/api/workspace/cues/update", {
      method: "POST",
      body: JSON.stringify({ cues }),
    }),

  styles: () => req<{ styles: StyleProfile[] }>("/api/styles"),
  saveStyle: (style: StyleProfile) =>
    req<{ style: StyleProfile }>("/api/styles", {
      method: "POST",
      body: JSON.stringify(style),
    }),
  deleteStyle: (name: string) =>
    req<{ deleted: boolean }>(`/api/styles?name=${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),

  jobs: () => req<{ jobs: JobSummary[] }>("/api/jobs"),
  job: (id: string) => req<JobDetail>(`/api/jobs/${id}`),

  transcribe: (body: TranscribeRequest) =>
    req<{ job_id: string }>("/api/jobs/transcribe", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  translate: (body: TranslateRequest) =>
    req<{ job_id: string }>("/api/jobs/translate", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  render: (body: RenderRequest) =>
    req<{ job_id: string }>("/api/jobs/render", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  filesTree: (folder: string) =>
    req<{ folder: string; entries: FileEntry[] }>(
      `/api/files/tree?folder=${encodeURIComponent(folder)}`,
    ),
  filesDelete: (path: string) =>
    req<{ ok: boolean }>(`/api/files?path=${encodeURIComponent(path)}`, {
      method: "DELETE",
    }),
  filesCleanup: () =>
    req<{ ok: boolean; removed: number; freed: number }>("/api/files/cleanup", {
      method: "POST",
    }),

  fonts: () => req<{ fonts: string[] }>("/api/fonts"),

  config: () =>
    req<{ path: string; model_root: string; output_dir: string; values: Record<string, unknown>; api_key_masked: string }>(
      "/api/config",
    ),
  saveConfig: (values: Record<string, unknown>) =>
    req<{ ok: boolean; path: string }>("/api/config", {
      method: "PUT",
      body: JSON.stringify({ values }),
    }),
};

// ---------- SSE 进度流 ----------
export interface ProgressEvent {
  status: string;
  status_tag: string;
  stage: string;
  progress: number;
  message: string;
  logs: string[];
  elapsed: number;
  error: string;
}

export interface DoneEvent {
  status: string;
  result: Record<string, unknown>;
  error: string;
}

export function subscribeJobEvents(
  jobId: string,
  onProgress: (e: ProgressEvent) => void,
  onDone: (e: DoneEvent) => void,
  onError?: (err: Event) => void,
): () => void {
  const es = new EventSource(`/api/jobs/${jobId}/events`);
  es.addEventListener("progress", (ev) => {
    try {
      onProgress(JSON.parse((ev as MessageEvent).data));
    } catch {
      /* ignore */
    }
  });
  es.addEventListener("done", (ev) => {
    try {
      onDone(JSON.parse((ev as MessageEvent).data));
    } catch {
      /* ignore */
    } finally {
      es.close();
    }
  });
  es.onerror = (ev) => {
    onError?.(ev);
    es.close();
  };
  return () => es.close();
}
