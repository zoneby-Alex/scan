# Video Analyzer

本地 Web 工具：粘贴 YouTube / Bilibili 视频 URL → 自动提取字幕 → AI 分析 → Obsidian 集成 → RAG 对话问答。

**定位**：实体软件，核心竞争力在 GPU 本地转录 + Obsidian 知识库集成 + 全隐私闭环。详见 [`迭代文档/未来发展.md`](迭代文档/未来发展.md)。

---

## 快速开始

```bash
cd D:\project\claude\scan
pip install -r requirements.txt          # 首次
taskkill //F //IM python.exe 2>nul       # 停旧进程
python -c "import uvicorn; uvicorn.run('src.web.server:app', host='127.0.0.1', port=8787)"
```

浏览器打开 `http://127.0.0.1:8787`，粘贴视频 URL，点击解析。

---

## 前置依赖

| 依赖 | 用途 |
|------|------|
| Python 3.12+ | 运行环境 |
| CUDA Toolkit 12+ (可选) | GPU 语音识别加速 |
| DeepSeek API Key | LLM 分析 (`.env` 配置) |
| Hugging Face 网络 (可选) | 首次下载模型 |

`requirements.txt` 生成方式：

```bash
pip install fastapi uvicorn httpx youtube-transcript-api yt-dlp chromadb \
    sentence-transformers diskcache faster-whisper pydantic-settings anthropic \
    opencc-python-reimplemented nvidia-cublas-cu12
```

---

## 配置

`.env` 文件：

```env
ANTHROPIC_AUTH_TOKEN=sk-your-deepseek-key
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_MODEL=deepseek-v4-flash[1m]
ANTHROPIC_REASONING_MODEL=deepseek-v4-flash[1m]

# Obsidian Vault 集成（选填，不填则输出到项目 output/ 目录）
OUTPUT_DIR=D:\tool\ob\know\0604\视频笔记
OBSIDIAN_VAULT=D:\tool\ob\know\0604

# Bilibili cookie 文件（遇 412 错误时配置，选填）
# BILIBILI_COOKIES=bilibili_cookies.txt
```

完整配置项见 `.env.example`。

---

## 功能

### 支持的平台

| 平台 | 字幕提取 | 语音识别回退 |
|------|:--:|:--:|
| YouTube (CC字幕) | ✅ youtube-transcript-api | ✅ Whisper |
| YouTube (无字幕) | ❌ | ✅ Whisper 自动回退 |
| Bilibili (CC字幕) | ✅ B站 API + WBI 签名 | ✅ Whisper |
| Bilibili (无CC字幕) | ❌ | ✅ Whisper + playurl API 直连 |

### 产出物 (每视频)

| 文件 | 内容 |
|------|------|
| `subtitles.md` | 完整字幕，带时间戳，YAML frontmatter (title/platform/date) |
| `overview.md` | 一句话总结 + 章节划分 + 详细摘要，含 `[[wikilink]]` 概念 |
| `keypoints.md` | 重点内容表 (时间/内容/★重要度)，自动分类标签 |
| `meta.json` | 元数据 (title/author/url/thumbnail/concepts/tags/category) |
| `subtitles.srt` | SRT 格式字幕，可导入播放器 |

外文视频自动翻译为中文对照（英文上+灰色中文下）。

### RAG 对话问答

分析完成后可在页面底部对视频内容提问，基于 ChromaDB 向量检索定位相关片段，LLM 回答带时间戳引用。

### Obsidian 集成

- 设置 `OUTPUT_DIR` → 分析结果直写 Obsidian Vault
- YAML frontmatter (title/platform/date/concepts) → Dataview 可查询
- LLM 自动提取 3-5 个核心概念 → `[[wikilink]]` → Graph View 双向链接
- 历史记录侧栏按仓库路径分组折叠，颜色区分来源

---

## 架构

```
URL → 平台匹配 → 字幕提取(3级回退)
  → 预处理(清洗+去重+合并+分段+繁→简)
  → LLM分析(摘要+重点提取+概念提取+分类标签)
  → 语言检测(外文→翻译)
  → 输出(.md ×3 + .srt + meta.json) → Obsidian Vault
  → ChromaDB RAG索引
  → Web展示 + RAG问答
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
     ├── medium + cuda/int8_float16 (首选)
     ├── small + cuda/float16 (回退)
     └── small + cpu/int8 (CPU兜底)
```

**Bilibili 特殊处理**：yt-dlp 对 `www.bilibili.com` 会被 412 拦截，音频下载改用 `x/player/playurl` API 获取 DASH 流直链，httpx 下载绕过 yt-dlp。

### LLM 调用策略

| 阶段 | 模型 | 说明 |
|------|------|------|
| 摘要生成 | deepseek-v4-flash[1m] | 长视频动态 Map-Reduce (双层) |
| 重点提取 | deepseek-v4-flash[1m] | json_schema 约束，分块去重 Top 20 |
| 概念提取 | deepseek-v4-flash[1m] | 3-5 个核心术语 → `[[wikilink]]` |
| 分类标签 | deepseek-v4-flash[1m] | 自动分类 (category + tags) |
| 外文翻译 | deepseek-v4-flash[1m] | CJK < 30% 触发翻译 |
| RAG 问答 | deepseek-v4-flash[1m] | 检索 top-5 + 生成 |

### 语音识别配置

| 条件 | 模型 | 显存 | 速度 (26min视频) |
|------|------|:--:|:--:|
| GPU (RTX 3050 Ti, 4GB) | medium + cuda int8_float16 | ~3 GB | ~5 分钟 |
| GPU 回退 | small + cuda float16 | ~2 GB | ~4 分钟 |
| CPU 回退 | small + int8 | — | ~10 分钟 |

纯本地计算，不消耗 API 额度。语言自动检测 (`language=None`) 替代强制中文，中英混合视频更准。

---

## 项目结构

```
scan/
├── .env                        # API 配置
├── .env.example                # 配置模板
├── pyproject.toml              # 依赖声明
├── requirements.txt            # pip freeze
├── README.md
├── src/
│   ├── models.py               # 数据模型
│   ├── config.py               # 配置管理 (pydantic-settings)
│   ├── llm.py                  # LLM 调用封装 (Anthropic SDK → DeepSeek)
│   ├── cache.py                # diskcache 磁盘缓存
│   ├── transcriber.py          # Whisper 语音识别 (faster-whisper + CTranslate2)
│   ├── translate.py            # 语言检测 + 中英翻译
│   ├── classify.py             # LLM 自动分类 + 标签
│   ├── extractors/             # 平台提取器
│   │   ├── base.py             # BaseExtractor 抽象类
│   │   ├── youtube.py          # YouTube (youtube-transcript-api + yt-dlp)
│   │   └── bilibili.py         # Bilibili (B站API + WBI + playurl)
│   ├── preprocess/             # 字幕预处理
│   │   ├── cleaner.py          # HTML清洗 + 繁→简 (opencc)
│   │   ├── dedup.py            # 相邻相似度去重
│   │   └── segmenter.py        # 短句合并 + 语义分段
│   ├── analyzers/              # LLM 分析
│   │   └── summarizer.py       # 摘要 + 重点提取 + 概念提取
│   ├── rag/                    # 检索增强生成
│   │   ├── vectorstore.py      # ChromaDB 向量存储
│   │   └── chat.py             # RAG 问答逻辑
│   ├── output/                 # 输出生成
│   │   ├── srt.py              # SRT 字幕格式化
│   │   └── markdown.py         # .md 生成 (含 YAML frontmatter)
│   └── web/                    # Web 服务
│       ├── server.py           # FastAPI (9个路由 + SSE 5事件类型)
│       └── static/
│           └── index.html      # 单页前端 (侧栏分组+主题+进度+双语渲染)
├── output/                     # 旧解析结果 (未配置 OUTPUT_DIR 时使用)
├── tempvideo/                  # 临时音频 (自动清理)
├── .cache/                     # diskcache 缓存
├── .chromadb/                  # ChromaDB 向量索引
└── 迭代文档/                   # 文档
    ├── 流程图.md               # Mermaid 流程图
    ├── 问题记录.md             # 23→25 条问题追踪
    ├── 经验总结.md             # 架构/工程经验
    ├── 优化升级方向.md         # 已完成 + 待做清单
    └── 未来发展.md             # 实体软件定位、演进路径、Electron方案
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | Web 页面 |
| POST | `/api/analyze?url=` | 分析视频 |
| GET | `/api/progress/{task_id}` | SSE 进度流 |
| GET | `/api/download/{filepath}` | 下载 .md 文件 |
| GET | `/api/history` | 历史记录列表 (双目录合并) |
| GET | `/api/history/{name}` | 历史详情 |
| DELETE | `/api/history/{name}` | 删除历史记录 |
| GET | `/api/chat?task_id=&q=` | RAG 问答 |

## 注意事项

- **Bilibili 412**: 已通过 UA 轮换 + WBI 签名 + cookie 支持 + playurl API 直连解决；遇 412 可配置 `BILIBILI_COOKIES`
- **Bilibili 无 CC 字幕**: 大部分 B 站视频无 CC 字幕，自动走 Whisper + playurl API 下载音频
- **模型首次下载**: medium 模型 (~1.5GB) 需 Hugging Face 网络，已配置代理可加速
- **长视频 (12h)**: 已支持动态 Map-Reduce + 双层摘要 + SSE 900s 超时
- **CUDA**: 需安装 NVIDIA CUDA Toolkit 12+ 和 `nvidia-cublas-cu12` pip 包
- **ffmpeg**: 非必需，音频下载使用原生格式
- **端口占用**: 启动前 `taskkill //F //IM python.exe` 清理残留进程

## License

MIT
