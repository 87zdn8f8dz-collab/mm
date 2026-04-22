# Cloud Social Crawler

批量抓取 Twitter(X) / Instagram / Facebook 公开账号素材，并在云端自动完成：

- 图片、视频、文案抓取
- 去重（近重复 + 中心内容一致但边缘水印差异）
- 水印冗余素材过滤（优先保留清晰度更高、边角干扰更低版本）
- 按平台归档到 `twitter素材/`、`ig素材/`、`facebook素材/`，并输出 `纯文案文本/`
- 保存原始 `title` / `description` / `content`（含源 metadata 备份）
- IG 账号自动预探测（优先选择可抓取账号）
- IG 专属抓取参数（更长超时、更低并发、失败重试）
- IG 抓取优先走 `/posts/` 路径，减少账号查询风控失败
- IG 抓取失败自动触发 Playwright 备用链路（浏览器兜底）
- 自动发现 50 个垂类账号（X/IG）
- 自动导出全网高热作品榜（视频/图文/文案）
- GitHub Actions 定时后台挂机运行

## 1) 目录结构

```text
cloud-social-crawler/
  ├─ config/
  │  ├─ accounts.example.yaml
  │  └─ accounts.yaml            # 可选，建议通过 Secret 注入
  ├─ src/
  │  └─ pipeline.py
  ├─ data/
  │  ├─ raw/
  │  ├─ archive/
  │  └─ rejected/
  └─ .github/workflows/cloud-social-crawler.yml
```

## 2) 账号配置

编辑 `config/accounts.example.yaml`（或提供 `config/accounts.yaml`）：

```yaml
twitter:
  - nasa
instagram:
  - natgeo
facebook:
  - nasa
crawl:
  max_items_per_account: 40
  sleep_request_seconds: 1.2
  command_timeout_seconds: 180
  max_workers: 4
  instagram_max_items_per_account: 6
  instagram_sleep_request_seconds: 2.2
  instagram_command_timeout_seconds: 180
  instagram_max_workers: 2
  facebook_max_items_per_account: 20
  facebook_sleep_request_seconds: 1.0
  facebook_command_timeout_seconds: 180
  facebook_max_workers: 2
  instagram_playwright_fallback_enabled: true
  instagram_playwright_fallback_timeout_seconds: 300
  instagram_playwright_fallback_headless: true
  retries: 1
  retry_backoff_seconds: 4
```

## 3) 本地快速验证（可选）

```bash
bash scripts/bootstrap_linux.sh
python3 src/pipeline.py --config config/accounts.example.yaml --data-root data
```

## 4) 云端部署（GitHub Actions）

把项目推到 GitHub 后，工作流会支持：

- 手动触发：`workflow_dispatch`
- 定时触发：每 6 小时

建议配置的仓库 Secrets：

- `ACCOUNTS_YAML_B64`：账号配置文件 base64 后内容（可选）
- `GALLERY_DL_COOKIES_B64`：cookies 文本（Netscape 格式）base64 后内容（可选但推荐）
- `FACEBOOK_GRAPH_ACCESS_TOKEN`：Facebook Graph API Access Token（抓取 Facebook 必填）
- `AUTO_DISCOVER`：是否开启自动发现账号（可选，默认 `1`）
- `X_DISCOVERY_KEYWORDS`：X 关键词，`|` 分隔（可选）
- `IG_DISCOVERY_TAGS`：IG 标签词，`|` 分隔（可选）

当 `AUTO_DISCOVER=1` 且发现脚本生成了 `config/accounts.auto.yaml` 时，
工作流优先使用自动发现账号；否则回退到 `config/accounts.yaml`（若存在）。

### Secret 生成示例

```bash
base64 -i config/accounts.yaml | pbcopy
base64 -i cookies.txt | pbcopy
```

然后将复制内容分别填入对应 Secret。

## 5) 产物说明

工作流执行完成后，在 Artifacts 下载：

- `data/archive/twitter素材/...`
- `data/archive/ig素材/...`
- `data/archive/facebook素材/...`
- `data/archive/纯文案文本/...`
- `data/archive/hot_content/top_works.json`（高热榜）
- `data/archive/hot_content/top_works.csv`
- `data/archive/hot_content/top_works.md`
- `data/archive/discovery/discovery_report.json`（自动发现报告）
- `data/rejected/...`（被判定为重复或水印冗余）

## 6) 重要说明

- 仅针对公开内容，需遵守平台条款与当地法律法规。
- 若平台风控增强，建议提供有效 cookies 并降低频率。
- Facebook 抓取走 Graph API，需配置 `FACEBOOK_GRAPH_ACCESS_TOKEN`。
- 该方案是“工程可运行版”，不是平台官方 API 替代品。
- 默认 `FAIL_ON_EMPTY=1`，当抓取结果为空会让任务失败，避免“表面成功、实际无数据”。

## 7) IG 登录态健康检查（新增）

新增工作流：`.github/workflows/ig-healthcheck.yml`，支持：

- 手动触发：`workflow_dispatch`
- 定时触发：每 3 小时

用途：

- 快速检测当前 IG cookies 是否可用
- 生成健康报告：`data/archive/ig_health/ig_health_report.json`
- 当可用账号数 `< require_success_min` 时，任务标记失败（默认至少 1 个）

说明：

- 主抓取流程已经接入 IG 预探测；当 IG 不健康时会自动降级为 X-only，保障批量任务稳定产出。
