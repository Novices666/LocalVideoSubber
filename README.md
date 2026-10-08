# LocalVideoSubber

本地视频字幕处理工具，支持：

- faster-whisper / whisper.cpp 语音转录
- OpenAI 兼容接口字幕翻译
- SRT / VTT / ASS 导出
- 软字幕封装与硬字幕烧录
- 本地 ASR 模型下载

提供 **Web UI**（React + FastAPI）与 **命令行** 双入口，核心流水线共享同一套 `lvs` 后端。

## 安装

后端依赖：

```bash
pip install -r requirements.txt
```

需要系统安装 FFmpeg，并确保 `ffmpeg`、`ffprobe` 在 PATH 中。

## Web UI 启动

一键启动（自动装依赖、构建前端、开浏览器）：双击 `start.bat`。

手动启动：

```bash
# 1. 安装并构建前端（需要 Node.js）
cd web
npm install
npm run build
cd ..

# 2. 启动后端（同时托管前端产物）
python -m uvicorn lvs.api:app --app-dir src --host 127.0.0.1 --port 8000
```

浏览器访问 http://127.0.0.1:8000 。

前端开发模式（热重载）：

```bash
# 终端 1：后端
python -m uvicorn lvs.api:app --app-dir src --port 8000

# 终端 2：前端 dev server（自动代理 /api 到 8000）
cd web && npm run dev
# 访问 http://127.0.0.1:5173
```

### 页面结构

七个页面，每页一屏：

| 页面 | 职责 |
| :--- | :--- |
| 主页 | 环境自检、系统状态、任务队列、快捷入口 |
| 转录 | 视频转字幕，配置与进度 |
| 翻译 | 字幕翻译，配置与进度 |
| 渲染 | 导出 / 软封装 / 硬烧录，样式选择 |
| 字幕设置 | 字幕样式规范文件（JSON）的增删改，实时预览 |
| 文件 | 工作目录浏览、预览、删除、一键清理中间产物 |
| 设置 | 全局项（模型目录、请求超时、工作目录、保留中间产物） |

字幕样式规范保存在 `styles/*.json`，渲染页从中选择复用。

## 工作目录

产物按视频分组存放在 `workspace/`（可在 `config.yaml` 的 `jobs.output_dir` 修改）：

```
workspace/
└── 视频名/
    ├── upload/       上传的原视频（重名加 _1/_2）
    ├── transcribe/   转录工作文件（J0001/J0002 阶段独立编号）
    ├── translate/    翻译工作文件
    ├── burn/         烧录工作文件
    └── output/       成品（各阶段最新成果汇总）
```

「文件」页可浏览整个工作目录，预览字幕/视频/音频，删除单个文件或一键清理中间产物。

## 模型

查看模型目录和可下载清单：

```bash
python scripts/download_models.py --list
python scripts/download_models.py --status
```

下载 ASR 模型：

```bash
python scripts/download_models.py --asr large-v3-turbo
python scripts/download_models.py --asr tiny
```

> 下载器仅用于转录（ASR）模型。翻译模型请用 LM Studio 等工具自行下载，然后在翻译页填写服务地址与模型名即可。

模型默认放在 `models/`，可在 `config.yaml` 中通过 `model_root` 修改。

## 环境自检

```bash
python -m lvs.cli check
```

或者在项目根目录使用：

```bash
lvs check
```

## 翻译服务

翻译使用 OpenAI 兼容接口。以 LM Studio 为例：

```yaml
translate:
  base_url: http://127.0.0.1:1234/v1
  api_key: ""
  model: qwen/qwen3.5-9b
  disable_thinking: true
```

关闭思考模式时，客户端会发送：

```json
{
  "reasoning_effort": "none",
  "chat_template_kwargs": {"enable_thinking": false}
}
```

## Python 调用

核心流程可以直接从 Python 调用：

```python
from lvs.config import load_config
from lvs.jobs import JobQueue
from lvs.pipeline import register_all

cfg = load_config()
queue = JobQueue()
register_all(queue)
queue.start()

job = queue.submit("transcribe", {
    "cfg": cfg,
    "video": "input.mp4",
    "model_path": None,
    "engine": "faster-whisper",
    "overrides": {},
    "seg_opts": {"enabled": True},
}, title="转录")
```

翻译和渲染任务也通过 `JobQueue.submit()` 提交，任务状态、进度、日志和取消能力由 `lvs.jobs` 提供。

## 测试

```bash
python -m pytest -q
python -m compileall -q src scripts tests
```
