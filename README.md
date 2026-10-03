# Irish News

爱尔兰新闻简报网站与受保护的发布 API。生产服务使用 **FastAPI + SQLite + Uvicorn**，现有外部入口为 Nginx 5310，应用监听 127.0.0.1:8200。

## 关键行为

- 每期按 Dublin 时区固定在 00:00 / 06:00 / 12:00 / 18:00。
- `POST /api/news/publish` 只接受 Bearer Token 认证。
- `published_at` 由服务器在数据库事务成功时生成；网页“是否陈旧”只看真实发布成功时间，不看模型生成时间。
- 同一个 slug + 完全相同内容可安全重试，返回 `status=exists`。
- 同一个 slug + 不同内容返回 HTTP 409，禁止静默覆盖历史。
- 跨期相同 `event_key` 或相同文章 URL 默认返回 HTTP 409。
- 同一真实事件确有新事实时，保留原 `event_key`，设置 `is_update=true`，并提供 `update_note`。
- SQLite 写入使用 `BEGIN IMMEDIATE`、WAL、foreign_keys 和 busy_timeout，避免并发写入破坏数据。
- 历史期数、上一期/下一期、已读/未读标记继续保留。

## 公开读取接口

- `GET /health`
- `GET /api/latest`
- `GET /api/editions`
- `GET /api/events/recent?days=14`

## 发布接口

`POST /api/news/publish`

Header:

```
Authorization: Bearer <secret>
Content-Type: application/json
```

请求格式见 `example-edition.json`。新一期 slug 必须与 Dublin 排期一致，例如：

```
2026-10-03-0600
```

标题必须严格使用：

```
🇮🇪 爱尔兰新闻简报｜2026-10-03 06:00｜住房 & IT 优先
```

## Token

生产服务器默认从：

```
/home/ubuntu/ireland-news/.publish_token
```

读取 Token，也可通过 `IRELAND_NEWS_PUBLISH_TOKEN` 环境变量覆盖。Token 文件必须权限 600，并且永远不要提交 GitHub。

生成示例：

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > .publish_token
chmod 600 .publish_token
```

## 本地运行

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > .publish_token
chmod 600 .publish_token
uvicorn app:app --host 127.0.0.1 --port 8200
```

本机发布：

```bash
./publish.py example-edition.json
```

## 生产部署原则

1. 先备份 `news.db` 与当前代码。
2. 在临时数据库上运行导入/迁移测试。
3. 更新代码后重启 Uvicorn 服务。
4. 验证 `/health`、首页、历史页、鉴权失败、真实 POST、幂等重试。
5. 最后再把定时任务切到发布 API。

外网发布接口必须使用公网可信 TLS。自签名证书只适合人工浏览/内网测试，不应作为自动发布连接器的最终入口。

## 数据文件

`news.db`、WAL/SHM、Token 和备份均在 `.gitignore` 中，不进入仓库。


## 临时进程守护

正常生产环境优先由 `ireland-news.service` 管理 Uvicorn。若维护连接没有 systemd 启动权限，可安装 `deploy/ensure-running.sh` 到应用目录，并在用户 crontab 每分钟调用一次作为故障兜底：

```cron
* * * * * /home/ubuntu/ireland-news/ensure_running.sh
```

该脚本只在 Uvicorn 进程不存在时启动备用进程，并用 `flock` 防止并发重复启动。它是故障兜底，不替代 systemd。
