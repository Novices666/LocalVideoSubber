# LocalVideoSubber 架构说明

本地视频字幕工作台：视频/音频 → 转录 → 翻译 → 渲染。提供 **Web UI**（React + FastAPI）与 **命令行** 双入口，共用同一套 `lvs` 后端。

## 技术栈

| 层 | 选型 |
| :--- | :--- |
| 前端 | React 19 + Vite + TypeScript + Tailwind CSS v4 + Radix UI（shadcn 风格组件）+ lucide-react 图标 |
| 后端 | Python 3.12 + FastAPI + SSE 进度推送 |
| 语音识别 | faster-whisper（CTranslate2，CUDA）/ whisper.cpp（CPU 降级） |
| 翻译 | OpenAI 兼容接口（llama.cpp / Ollama / LM Studio / vLLM） |
| 媒体处理 | ffmpeg / ffprobe（NVENC 硬编） |

## 分层

```
┌──────────────────────────────────────────────┐
│  web/  React 前端（六页，每页一屏）              │
│  主页 / 转录 / 翻译 / 渲染 / 字幕设置 / 设置       │
└──────────────────┬───────────────────────────┘
                   │ REST + SSE（/api/*）
┌──────────────────▼───────────────────────────┐
│  lvs.api  FastAPI 应用                        │
│  任务提交 / 进度 SSE / 样式 CRUD / 配置读写 / 上传 │
└──────┬───────────────────┬───────────────────┘
       │                   │
┌──────▼─────────┐  ┌──────▼────────────────────┐
│ lvs.workspace  │  │ lvs.jobs                  │
│ 全局工作区状态   │  │ 单 worker 串行队列 + 取消     │
└──────┬─────────┘  └──────┬────────────────────┘
       │                   │ 按 kind 分发
       │           ┌───────▼────────────────────┐
       │           │ lvs.pipeline 编排层          │
       │           │ transcribe/translate/render │
       │           └──┬──────┬──────┬───────────┘
       │              │      │      │
       │        ┌─────▼──┐ ┌─▼────┐ ┌▼────────┐
       │        │lvs.asr │ │trans │ │subtitle │
       │        │转录引擎 │ │翻译  │ │断句/渲染 │
       │        └────────┘ └──────┘ └─────────┘
       │                        ▲
       │                 ┌──────┴──────┐
       └────────────────►│ lvs.styles  │  字幕样式规范文件（styles/*.json）
                          └─────────────┘

lvs.config（配置/模型路径解析/环境自检）与 lvs.media（ffmpeg 封装）被所有层依赖。
```

## 模块

| 模块 | 责任 |
| :--- | :--- |
| `lvs.api` | FastAPI 应用：任务提交、SSE 进度流、样式 CRUD、配置读写、文件上传、静态前端托管 |
| `lvs.workspace` | 全局工作区：当前字幕/视频/产物，跨页面流转（转录→翻译→渲染自动带入） |
| `lvs.styles` | 字幕样式规范文件：`styles/*.json` 的增删改，横屏/竖屏方向，内置预设 |
| `lvs.config` | YAML 配置、模型路径三级回退、环境自检（含 cuBLAS/Blackwell 检测） |
| `lvs.media` | ffmpeg/ffprobe 封装：探测、抽音频、切片、可取消子进程 |
| `lvs.jobs` | 单 worker 串行任务队列、取消令牌、进度/日志事件 |
| `lvs.pipeline` | 任务编排：`run_transcribe` / `run_translate` / `run_render` / `run_resegment` |
| `lvs.asr` | `faster_whisper_engine`（CUDA，Blackwell 自动降级 float16）、`whispercpp_engine` |
| `lvs.translate` | `llm_client`（OpenAI 兼容+重试+JSON 容错）、`pipeline`（分批/对齐/降级重试）、`prompts` |
| `lvs.subtitle` | `models`（Word/Segment/Cue）、`io`（SRT/VTT/ASS）、`segmenter`（智能断句）、`render`（导出/软封装/硬烧录） |
| `lvs.cli` | 命令行入口：`lvs check` / `lvs download` / `lvs test` |
| `web/` | React 前端（见 `web/src/`） |
| `scripts/download_models.py` | 模型下载器（ASR/GGML/GGUF，含 Qwen3.5-9B） |

## 数据流

### 任务执行

```
前端提交 → POST /api/jobs/{kind} → JobQueue.submit → worker 串行消费
  → pipeline runner → 各阶段进度写入 Job → SSE 推送到前端 → 完成后结果吸入 workspace
```

### 跨页流转（工作区）

```
转录完成 → workspace.cues（字幕）
  ↓
翻译页读取 cues → 翻译完成 → 译文合并回 workspace.cues
  ↓
渲染页读取 cues → 选择样式 profile → 渲染 → 产物写入 workspace.last_output
```

工作区在内存中，每步也落盘到 `output/<视频名>/<任务号>/`，可手动换文件。

### 字幕样式规范

```
styles/*.json（每个文件一个样式规范，含 orientation 横屏/竖屏）
  ↓ 渲染页下拉选择
StyleProfile.to_render() → {style, bilingual} → render 模块 → ASS 样式
```

## 关键设计决策

- **模型路径三级回退**：显式路径 → config 配置 → `models/<类型>/` 本地扫描 → HF 仓库名；配置失效时按名称模糊匹配本地完整模型。
- **智能断句**：用词级时间戳按标点/字数/停顿/时长四重约束重切字幕。
- **翻译对齐**：编号 JSON 回填 + 四层兜底（整批重发→补缺→拆半→逐条）。
- **双语渲染**：ASS 的 Source/Target 双样式，原文译文独立字号/描边/边距。
- **Blackwell 兼容**：RTX 50 系（sm_120）上 INT8 被禁用，自动降级 float16；环境自检检测 cuBLAS 与架构。
- **每页一屏**：主内容不滚动，字幕列表/日志等长内容局部滚动。

## 目录结构

```
LocalVideoSubber/
├── web/                     # React 前端
│   ├── src/
│   │   ├── pages/           # 六页
│   │   ├── components/      # 布局、进度、表格、上传、Toast、UI 组件
│   │   ├── hooks/           # useJob / useConfig
│   │   └── lib/             # api 封装、utils
│   └── package.json
├── src/lvs/                 # Python 后端包
├── scripts/download_models.py
├── styles/                  # 字幕样式规范文件（运行时生成）
├── models/                  # 模型（asr / translate，不进 git）
├── output/                  # 输出产物
├── design-system/           # 设计规范（ui-ux-pro-max 生成）
├── start.bat                # 一键启动
├── config.example.yaml
└── tests/
```
