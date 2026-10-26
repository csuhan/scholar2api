# Google Scholar 逆向分析报告

目标：给定 Scholar ID/URL，稳定获取学者数据并封装为 API。
分析对象：`https://scholar.google.com`（以 user=`vgcxKEcAAAAJ` 为实例，2026-10-25 实测）。

## 0. 关于分析工具（REA）

按建议评估了 [morluto/rea](https://github.com/morluto/rea)：它是面向**二进制/打包应用**
（native binary、Electron/JS app、APK，依托 Hopper/Ghidra/Frida）的逆向 MCP。
Scholar 是 Web 服务，"逆向"的正确层级是 **HTTP 端点 + HTML 结构 + 反爬行为**，
直接用 curl/httpx/Playwright 探测即可，REA 对此场景无能为力（其官方文档亦声明不用于
此类分析）。以下结论全部来自对真实端点的受控探测。

## 1. 端点清单（全部 GET，无鉴权）

| # | 端点 | 参数 | 防护强度 |
|---|---|---|---|
| 1 | `/citations?user={ID}` | `hl`, `cstart`, `pagesize`(≤100), `sortby=pubdate` | ★ 低 |
| 2 | `/citations?view_op=list_works&user={ID}` | `cstart`, `pagesize`（分页文章列表） | ★ 低 |
| 3 | `/citations?view_op=view_citation&user={ID}&citation_for_view={ID}:{cluster}` | 单篇详情 | ★ 低 |
| 4 | `/citations?view_op=search_authors&mauthors={name}` | 作者搜索 | ★★★ **需 Google 登录**（302→accounts.google.com） |
| 5 | `/scholar?cites={cluster_id}` | 被引列表，`start` 分页 | ★★★★ 极易触发验证码 |
| 6 | `/citations?view_op=list_colleagues&user={ID}` | 合作者 | ★ 低 |

关键结论：**`/citations` 系列（学者维度数据）防护很弱**，curl + 浏览器 UA 即可；
**`/scholar` 搜索系列防护极强**。scholar2api 的核心设计原则就是——
只用 `/citations` 端点满足需求，把 `/scholar` 留给可选的浏览器通道。

## 2. 页面结构与解析锚点（实测验证）

### 2.1 主页 `/citations?user=ID`（约 160KB HTML）

| 数据 | 锚点 |
|---|---|
| 姓名 | `<div id="gsc_prf_in">` |
| 单位/职称 | 第一个 `<div class="gsc_prf_il">` |
| 验证邮箱域名 | `<div id="gsc_prf_ivh">` 内 "Verified email at X" |
| 主页链接 | `<a class="gsc_prf_ila">` |
| 研究兴趣 | `<div id="gsc_prf_int">` 内 `<a>` |
| 统计表 | `<table id="gsc_rsb_st">`：行 `.gsc_rsb_sc1`（Citations/h-index/i10-index），值 `.gsc_rsb_std`（All / Since YYYY） |
| 年度引用直方图 | 年份 `<span class="gsc_g_t" style="right:Npx">`，数值 `<a class="gsc_g_a" style="right:N+5px">` 内 `.gsc_g_al`（**bar 偏移 = 年份偏移 + 5px**，按此配对最稳） |
| 合作者 | `id="gsc_rsb_co"` 的 `<ul>` 内 `<a href="/citations?user=...">姓名</a>` + `.gsc_rsb_a_ext`（单位） |
| 文章行 | `<tr class="gsc_a_tr">`：标题 `.gsc_a_at`（href 含 `citation_for_view=ID:cluster`）、作者/期刊 `.gs_gray`×2、引用数 `a.gsc_a_ac`（**href 在 class 之前**，含 `cites=cluster_id`）、年份 `.gsc_a_h` |

### 2.2 单篇详情 `view_op=view_citation`

字段对 `<div class="gsc_oci_field">/<div class="gsc_oci_value">`（Authors、Publication
date、Journal/Conference、Publisher、Description、Total citations）；
标题 `#gsc_oci_title`；PDF `.gsc_oci_title_ggi a`；年度引用 `.gsc_oci_g_t/.gsc_oci_g_al`
（同样 +5px 配对）；被引 cluster id 从 `cites=(\d+)` 提取。

## 3. 反爬机制（本报告核心）

### 3.1 观察到的四种封锁形态

| 形态 | 触发场景（实测） | 特征 | 对策 |
|---|---|---|---|
| ① 页内 reCAPTCHA v2 | curl 请求 `/scholar?cites=` 数次后 | 200 但内容是 `<form id="gs_captcha_f">` + `grecaptcha.render("gs_captcha_c", {sitekey:"6LfFDwUTAAAA..."})`；**求解后表单自动 GET 回原文** | Playwright 点击 checkbox → VLM 解图 |
| ② `/sorry/index` 硬封 | **headless Chromium** 首次访问即触发 | 302 到 `google.com/sorry/index?continue=...`，reCAPTCHA **Enterprise**；部分情况 JS "simple challenge" 几秒后自动放行 | 等待自动放行 → 否则 VLM 解；根治靠真实 Chrome + 持久 profile + 住宅代理 |
| ③ 登录墙 | `view_op=search_authors` | 302→`accounts.google.com/Login` | 无解（不该匿名爬）；改用其他端点替代 |
| ④ 429 | 高频请求 | HTTP 429 | 指数退避 + 换 IP |

### 3.2 指纹与信任分（实测对比）

同一 IP 同一时刻：
- `curl + 桌面 UA` → `/citations` 正常 ✅
- `Playwright headless-shell`（仅去 webdriver 标记）→ **立即 `/sorry/index`** ❌
- `Playwright channel="chrome"`（真实 Chrome）+ 持久 profile → 正常 ✅，并获得 `NID`/`GSP` cookies

结论：Google 对**浏览器指纹**（headless-shell 的插件/WebGL/权限 API 等数十个特征）
和 **cookie 信任累积** 的权重极高。反检测优先级：
`真实 Chrome > 持久化 user_data_dir > stealth init 脚本 > 住宅代理`。

### 3.3 reCAPTCHA v2 图像挑战的 VLM 求解（已实现）

流程（`fetchers/browser.py` + `solvers/vlm.py`）：
1. 定位 anchor iframe（`recaptcha.*anchor`），点击 `#recaptcha-anchor` checkbox
2. 挑战弹出（bframe iframe）：读 `.rc-imageselect-desc*` 指令与 `<strong>` 目标词
3. 逐块截图 **`.rc-image-tile-wrapper`（overflow-hidden 容器）**——关键细节：
   3×3 挑战中每个 `<img>` 都是同一张合成大图靠 CSS 裁剪，直接截 img 会把整图
   发给 VLM 导致全部误判；必须截容器的可见区域。截图前注入
   `*{animation:none!important}` 消除淡入动画保证元素稳定
4. 4×4 动态网格：点击后瓦片刷新，循环重截重判，直到无新目标
5. 点 `#recaptcha-verify-button`；`aria-checked=true` 或 `#g-recaptcha-response` 非空即成功
6. 表单自动提交 → 拿到内容 + cookies → **cookies 回注 httpx 客户端**，恢复廉价通道

VLM 走 OpenAI 兼容协议（实测 2026-10-25）：
- **OpenRouter**（默认）：`openrouter/free` 元模型 + 免费视觉模型 fallback 列表
  （gemma-4-31b/26b、dots-3-note-preview）。注意：免费档会路由到 reasoning
  VLM（答案在 `reasoning` 字段），且上游瞬时限流常见——客户端已做双字段解析、
  fallback 路由和重试。未充值 50 请求/天，一次验证码约 10~30 请求
- HuggingFace 免费推理：实测 Qwen2.5/3-VL 系列均不可用（无额度/不在免费档），
  仅作保留通道

### 3.4 成本与稳定性建议（部署侧）

1. **缓存是第一道防线**：学者数据天级变化，`CACHE_TTL≥1h` 可消掉 99% 请求
2. 请求 pacing：固定间隔 + 随机抖动（已实现 1s + 0~2s）
3. 数据中心 IP（阿里云/AWS）天然低信任分：住宅代理池是规模化唯一解
4. 持久化浏览器 profile 目录挂 volume，避免每次从零累积信任
5. 监控：对 503（blocked）率告警，触发即降速/换代理
6. 合规：仅抓公开数据、尊重 robots 与频率；大规模商用建议 SerpAPI 等付费通道兜底

## 4. 已知限制

- 作者搜索（`search_authors`）需登录，未实现（可用 Semantic Scholar/OpenAlex API 替代）
- 被引列表（`/scholar?cites=`）防护最强，仅走浏览器通道
- Google 不定期调整 class 名；`tests/` 内有 fixture 测试，失效时按 §2 重新标定

## 5. 容量边界（实测 2026-10-26，家庭/办公 IP，冷会话）

### 5.1 Google Scholar 反爬阈值

| 场景 | 强度 | 结果 |
|---|---|---|
| `/citations` 冷会话 | 0.5 rps × 15 | 全部 200 |
| `/citations` | 2 rps × 20 | 全部 200 |
| `/citations` | 5 rps × 20 | 全部 200 |
| `/citations` | 10 并发突发 | 全部 200（1.2~1.8s） |
| `/scholar?cites=` | 1 rps × 5 | 全部 200 |
| `/scholar?cites=` | 20 并发（≈10.6 rps） | 全部 200 |
| **`/citations` 持续 10 rps** | **246 次 / 115 秒** | **触发封锁**（302 → `/sorry/index`，客户端见 429） |

封锁特征：
- **IP 级**：`scholar.google.com` 与 `scholar.google.com.hk` 同时封；普通 Google 搜索
  不受影响；换 UA 无效；带持久 profile 的真实 Chrome 也无法通过
- 封锁页 = `google.com/sorry/index`（reCAPTCHA），不是页内 `#gs_captcha_f`
- **恢复时间 ≥ 35 分钟**（20 分钟与 35 分钟两次探测均未恢复；Google 硬封锁通常 30 分钟~数小时）

### 5.2 API 自身吞吐

| 指标 | 数值 |
|---|---|
| 缓存命中吞吐（并发 20） | **600~800 RPS**，p50 24ms，p95 <100ms（单 uvicorn worker） |
| 高并发（50/100） | 吞吐反降至 160~210 RPS（单 worker 饱和 + access log） |
| 未缓存单请求 | 0.4~1.8s（受 1~3s pacing 约束，约 0.5 req/s/实例） |
| 封锁期间首个未缓存请求 | ~24s（HTTP→429→浏览器→验证码失败） |
| 封锁期间后续请求（熔断） | **~10ms 返回 503** |

结论：**缓存层可承载上千 QPS 级别的读，未缓存的抓取能力约 0.5~5 req/s/IP**。
要扩大抓取吞吐只能加代理 IP 池；单 IP 10 rps 持续约 2 分钟即封。
