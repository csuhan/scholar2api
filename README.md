# scholar2api

给定 Google Scholar 学者 ID 或主页 URL，返回结构化 JSON：引用数、h-index、
i10-index、逐年引用、研究兴趣、合作者、论文列表与单篇详情。

Google Scholar 没有官方 API，本项目通过解析其公开页面实现，并内置缓存、
浏览器兜底与验证码自动识别。

```
GET /author/vgcxKEcAAAAJ  ->  { name, stats, papers, ... }
```

## 特性

- **零第三方解析库**：纯正则解析，15 个离线测试基于真实页面快照
- **三层抓取**：TTL 缓存 → httpx（快）→ Playwright + VLM 打码（仅在封锁时）
- **反爬就绪**：真实 Chrome、持久化 profile、代理、请求节奏控制
- **API 语义完整**：400 / 404 / 502 / 503 均正确映射

## 快速开始

```bash
pip install -e .
python -m playwright install chromium   # 只为反爬兜底，主链路不需要
uvicorn scholar2api.service:app --port 8000
```

打开 http://localhost:8000/docs 查看交互式文档。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/healthz` | 健康检查 |
| GET | `/author/{id}` | 学者主页（`id` 可为纯 ID 或 URL） |
| GET | `/author?url=...` | 同上，URL 用查询参数传入（避免路径编码问题） |
| GET | `/author/{id}/papers` | 论文列表；`cstart` `pagesize` `sortby=cited\|pubdate` `all_pages` |
| GET | `/author/{id}/paper/{cluster}` | 单篇详情（cluster 为 `citation_for_view` 冒号后的部分） |

```bash
curl 'http://localhost:8000/author/vgcxKEcAAAAJ' | jq
curl 'http://localhost:8000/author?url=https://scholar.google.com/citations?user=vgcxKEcAAAAJ'
curl 'http://localhost:8000/author/vgcxKEcAAAAJ/papers?sortby=pubdate&pagesize=20'
curl 'http://localhost:8000/author/vgcxKEcAAAAJ/paper/u5HHmVD_uO8C' | jq
```

响应节选：

```json
{
  "user_id": "vgcxKEcAAAAJ",
  "name": "Jiaming Han",
  "affiliation": "PhD Student, CUHK MMLab",
  "stats": {
    "citations": {"all": 7223, "since_5y": 7222},
    "h_index":   {"all": 16, "since_5y": 16},
    "i10_index": {"all": 18, "since_5y": 18},
    "since_year": 2021
  },
  "citations_per_year": {"2021": 72, "2022": 261, "2025": 2288},
  "papers": [
    {"title": "Align Deep Features for Oriented Object Detection",
     "citations": 1538, "year": 2021,
     "citation_for_view": "vgcxKEcAAAAJ:u5HHmVD_uO8C"}
  ]
}
```

## 配置

复制 `.env.example` 为 `.env`（真实环境变量优先）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `SCHOLAR2API_CACHE_TTL` | `3600` | 缓存秒数，**最有效的防封手段** |
| `SCHOLAR2API_PROXY` | – | 代理，如 `http://user:pass@host:port` |
| `SCHOLAR2API_VLM_PROVIDER` | `openrouter` | `openrouter` / `huggingface` |
| `OPENROUTER_API_KEY` | – | 打码用；不配也能跑主链路 |
| `SCHOLAR2API_VLM_MODEL` | `openrouter/free` | 免费视觉模型自动 fallback |
| `SCHOLAR2API_HEADLESS` | `1` | `0` 为有头模式（配 xvfb 更稳） |
| `SCHOLAR2API_CHANNEL` | – | `chrome` 使用真实 Chrome，显著降低被封率 |
| `SCHOLAR2API_USER_DATA_DIR` | – | 浏览器 profile 目录，累积信任分 |
| `SCHOLAR2API_BROWSER_TIMEOUT` | `300` | 单次浏览器抓取总超时（秒） |
| `SCHOLAR2API_BLOCK_COOLDOWN` | `120` | 硬封锁后快速失败的冷却秒数 |

## 反爬设计

| 场景 | 应对 |
|---|---|
| `/citations` 学者接口 | 几乎不触发验证码，HTTP 直连即可 |
| 页面内 reCAPTCHA v2 | Playwright 点击 + 瓦片截图交 VLM 判定 |
| `google.com/sorry/index` | 先等 JS 自动放行，否则走打码 |
| 指纹识别 | 优先真实 Chrome + 持久 profile（headless shell 会被秒封） |
| 高频请求 | 缓存 + 1~3s 随机 pacing + 429 退避 |

> **额度提示**：一次 4×4 验证码约消耗 48 次 VLM 请求，OpenRouter 免费额度
> 50/天仅够 1 次；生产建议充值或换付费 VLM。
> 完整逆向分析（端点、选择器、封锁形态实测）见 [docs/analysis.md](docs/analysis.md)。

## Docker

```bash
docker build -t scholar2api .
docker run -d -p 8000:8000 --env-file .env -v scholar-profile:/data scholar2api
```

## 开发

```bash
pip install -e ".[dev]"
pytest -q
ruff check scholar2api tests
```

## 免责声明

仅供学习与个人研究使用。请遵守 Google 服务条款与 robots 协议，控制请求频率；
大规模商用请使用官方或付费数据服务。
