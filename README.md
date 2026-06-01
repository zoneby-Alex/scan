# Video Analyzer

本地 Web 工具：粘贴 YouTube / Bilibili 视频 URL → 自动提取字幕 → AI 分析 → 3 份 `.md` 输出 + 对话问答。

## 快速开始

```bash
cd D:\project\claude\scan
pip install -r requirements.txt   # 首次
python -m src.web.server          # 启动
```

浏览器打开 `http://127.0.0.1:8787`，粘贴视频 URL，点击解析。

## 前置依赖

| 依赖 | 用途 |
|------|------|
| Python 3.12+ | 运行环境 |
| CUDA Toolkit 12+ (可选) | GPU 语音识别加速 |
| DeepSeek API Key | LLM 分析 (`.env` 配置) |

`requirements.txt` 生成方式：

```bash
pip install fastapi uvicorn httpx youtube-transcript-api yt-dlp chromadb \
    sentence-transformers diskcache faster-whisper pydantic-settings anthropic \
    opencc-python-reimplemented nvidia-cublas-cu12
```

## 配置

`.env` 文件：

```env
ANTHROPIC_AUTH_TOKEN=sk-your-deepseek-key
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_MODEL=deepseek-v4-flash[1m]
ANTHROPIC_REASONING_MODEL=deepseek-v4-pro
```

## 功能

### 支持的平台

| 平台 | 字幕提取 | 语音识别回退 |
|------|:--:|:--:|
| YouTube (CC字幕) | ✅ youtube-transcript-api | ✅ Whisper |
| YouTube (无字幕) | ❌ | ✅ Whisper 自动回退 |
| Bilibili (CC字幕) | ✅ B站开放API | ✅ Whisper |
| Bilibili (无CC字幕) | ❌ | ✅ Whisper 自动回退 |

### 产出物 (每视频)

| 文件 | 内容 |
|------|------|
| `subtitles.md` | 完整字幕，带时间戳，自动繁→简 |
| `overview.md` | 一句话总结 + 章节划分 + 详细摘要 |
| `keypoints.md` | 重点内容表 (时间/内容/★重要度) |

外文视频自动翻译为中文对照（英文上+灰色中文下）。

### 对话问答

分析完成后可在页面底部对视频内容提问，基于 ChromaDB 向量检索定位相关片段，LLM 回答带时间戳引用。

## 架构

```
URL → 平台匹配 → 字幕提取(3级回退)
  → 预处理(清洗+去重+合并+分段+繁→简)
  → LLM分析(摘要+重点提取)
  → 语言检测(外文→翻译)
  → 4文件输出(.md ×3 + meta.json)
  → ChromaDB RAG索引
  → Web展示
```

### 字幕提取：3级回退

```
1. youtube-transcript-api / B站API → CC字幕
     │
     ├── 失败
     ▼
2. yt-dlp 下载 SRT 字幕 (仅YouTube)
     │
     ├── 失败
     ▼
3. faster-whisper 本地语音识别 (GPU/CPU自动检测)
```

### LLM 调用策略

| 阶段 | 模型 | 说明 |
|------|------|------|
| 摘要生成 | deepseek-v4-flash[1m] | 长视频动态 Map-Reduce |
| 重点提取 | deepseek-v4-pro | json_schema 约束 |
| 外文翻译 | deepseek-v4-flash[1m] | 批量翻译 |
| RAG 问答 | deepseek-v4-flash[1m] | 检索 top-5 + 生成 |

### 语音识别配置

| 条件 | 模型 | 速度 (26min视频) |
|------|------|:--:|
| GPU (RTX 3050 Ti) | small + cuda float16 | ~5 分钟 |
| CPU (i5-11400H) | tiny + int8 | ~5 分钟 |

纯本地计算，不消耗 API 额度。

## 项目结构

```
scan/
├── .env                    # API 配置
├── src/
│   ├── models.py           # 数据模型
│   ├── config.py           # 配置管理
│   ├── llm.py              # LLM 调用封装
│   ├── transcriber.py      # Whisper 语音识别
│   ├── translate.py        # 语言检测 + 翻译
│   ├── cache.py            # 磁盘缓存
│   ├── extractors/         # 平台提取器
│   │   ├── youtube.py      # YouTube (3级回退)
│   │   └── bilibili.py     # Bilibili
│   ├── preprocess/         # 字幕预处理
│   │   ├── cleaner.py      # 清洗 + 繁→简
│   │   ├── dedup.py        # 去重
│   │   └── segmenter.py    # 分段
│   ├── analyzers/          # LLM 分析
│   │   └── summarizer.py   # 摘要 + 重点提取
│   ├── rag/                # 检索增强生成
│   │   ├── vectorstore.py  # ChromaDB
│   │   └── chat.py         # 问答逻辑
│   ├── output/             # 输出生成
│   │   └── markdown.py     # .md 文件
│   └── web/                # Web 服务
│       ├── server.py       # FastAPI
│       └── static/
│           └── index.html  # 单页前端
├── output/                 # 解析结果 (每视频一个子文件夹)
├── tempvideo/              # 临时音频 (自动清理)
└── 流程图.md               # Mermaid 流程图
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | Web 页面 |
| POST | `/api/analyze?url=` | 分析视频 |
| GET | `/api/progress/{task_id}` | SSE 进度流 |
| GET | `/api/download/{filepath}` | 下载 .md 文件 |
| GET | `/api/history` | 历史记录列表 |
| GET | `/api/history/{name}` | 历史详情 |
| DELETE | `/api/history/{name}` | 删除历史记录 |
| GET | `/api/chat?task_id=&q=` | RAG 问答 |

## 注意事项

- **Bilibili**: 大部分视频无 CC 字幕，自动回退 Whisper 语音识别
- **12h 长视频**: 已支持动态 Map-Reduce，摘要阶段会显示分块进度
- **CUDA**: 需安装 NVIDIA CUDA Toolkit 12+ 和 `nvidia-cublas-cu12` pip 包
- **ffmpeg**: 非必需，音频下载使用原生格式

## License

MIT
