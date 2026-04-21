# Cloud Social Crawler

批量抓取 Twitter(X) / Instagram 公开账号素材，并在云端自动完成：

- 图片、视频、文案抓取
- 去重（近重复 + 中心内容一致但边缘水印差异）
- 水印冗余素材过滤（优先保留清晰度更高、边角干扰更低版本）
- 按类型归档到 `images/`、`videos/`、`captions/`
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
crawl:
  max_items_per_account: 40
  sleep_request_seconds: 1.2
```

## 3) 本地快速验证（可选）

```bash
python3 -m pip install -r requirements.txt
python3 src/pipeline.py --config config/accounts.example.yaml --data-root data
```

## 4) 云端部署（GitHub Actions）

把项目推到 GitHub 后，工作流会支持：

- 手动触发：`workflow_dispatch`
- 定时触发：每 6 小时

建议配置的仓库 Secrets：

- `ACCOUNTS_YAML_B64`：账号配置文件 base64 后内容（可选）
- `GALLERY_DL_COOKIES_B64`：cookies 文本（Netscape 格式）base64 后内容（可选但推荐）

### Secret 生成示例

```bash
base64 -i config/accounts.yaml | pbcopy
base64 -i cookies.txt | pbcopy
```

然后将复制内容分别填入对应 Secret。

## 5) 产物说明

工作流执行完成后，在 Artifacts 下载：

- `data/archive/images/...`
- `data/archive/videos/...`
- `data/archive/captions/...`
- `data/rejected/...`（被判定为重复或水印冗余）

## 6) 重要说明

- 仅针对公开内容，需遵守平台条款与当地法律法规。
- 若平台风控增强，建议提供有效 cookies 并降低频率。
- 该方案是“工程可运行版”，不是平台官方 API 替代品。
- 默认 `FAIL_ON_EMPTY=1`，当抓取结果为空会让任务失败，避免“表面成功、实际无数据”。
