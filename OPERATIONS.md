# 采集与发布运维

网站只接收新闻 JSON。轮询一个静态 JSON 文件不会生成下一期新闻。

正式期次按 `Europe/Dublin` 的 00:00、06:00、12:00、18:00 计算，自动适配夏令时。生成端应在每个时点后运行，先查 `/api/editions/<slug>`，已经存在则不要重新生成或更改时间戳。同一期重试必须发送完全相同的 JSON。

## 生成端每次运行

1. 用服务器 `./.venv/bin/python task_context.py` 读取当前期次和最近 60 天的 JSON 去重历史。
2. 实时搜索并读取原文，优先 Greater Dublin 住房、规划、租赁，以及爱尔兰 IT、软件、AI 和招聘消息。通常取最近 24 小时，补发可扩展至 48 小时；不能把旧事实包装成新进展。
3. 写中文标题和 1–2 句中文摘要，保留准确的原文详情页链接和可验证的发布时间。没有发布时间证据则使用 `null`。只选可核实的实际消息，不填充数量。
4. 按 `event_key`、规范化 URL 和事件语义去重。真实的新进展沿用原 `event_key`，填写 `is_update=true` 和 `update_note`。遇到 409 应检查去重历史，不能通过随意换 key 或伪造 update 绕过。
5. 保存符合 README schema 的 UTF-8 JSON，再直接发送至服务器。发布 Token 只在服务器读取，不必同步新闻内容到 Google Drive 或另一个 GitHub 仓库。
6. 成功后读取 `/api/editions/<slug>` 和 `/health`，核对条目数、来源及 `missing_due_slot=false`；不能把仅仅运行完脚本或桥接的 `unchanged` 当成发布成功。

```sh
sh deploy/publish-over-ssh.sh outputs/edition.json
# 或在服务器运行：
./.venv/bin/python publish.py edition.json
# 也支持通过 stdin 传 JSON：
./.venv/bin/python publish.py - < edition.json
```

`IRELAND_NEWS_SSH_KEY` 和 `IRELAND_NEWS_SSH_TARGET` 可覆盖 SSH 默认配置。该发布脚本只发送 JSON，密钥和 Token 不进入数据文件或 GitHub。

如果没有符合条件的新消息，可以发布 `items: []` 的真实空期次。如果原文采集完全失败，必须报告采集失败，不能以空期次掩盖故障。

## 后台服务

网站使用 `deploy/ireland-news.service`，由 systemd 负责启动、重启和开机恢复。部署到 `/etc/systemd/system/` 后：

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now ireland-news.service
```

已有由 cron 拉起的 8200 端口进程需要先停止，并移除本项目的 watchdog cron，避免与 systemd 争抢端口。保留其他项目的 cron。

GitHub 桥接仅为可选输入，使用仓库内的 `github_bridge_poller.py` 及 `deploy/ireland-news-bridge.{service,timer}`。每分钟轮询，加入缓存绕过参数，允许空期次，始终向发布 API 对账。上游停更且网站缺期时返回失败；网站已有直接发布的更新期次时，返回 `superseded` 并保留 `source_stale=true`。桥接不能代替采集定时任务。不要同时运行桥接 cron 和 timer。

`/health` 的 `ok` 表示网站服务可访问，内容是否及时应检查 `stale`、`missing_due_slot`、`due_slug`。期次超过计划 30 分钟仍缺失即标记 stale，即使刚刚补发了一份旧期次也不会显示更新正常。

## Codex 定时采集

无需模型 API Key 的方案是在本聊天设置定时任务：每日 Dublin 时间 00:05、06:05、12:05、18:05 采集并通过 SSH 直接发布。需要保持这台电脑开机、Codex 可用及服务器连接权限有效；这不是脱离电脑运行的服务器新闻生成器。服务器独立生成需要另行配置模型服务和凭据。

运行记录应保存期次、来源、发布返回值及读取验证结果。已存在的期次保持安静；只在发布成功、失败或需要用户处理时通知。

## 验证与排查

```sh
./.venv/bin/python -m unittest discover -s tests -v
curl -fsS http://127.0.0.1:8200/health
sudo systemctl status ireland-news.service ireland-news-bridge.timer
sudo journalctl -u ireland-news-bridge.service -n 20 --no-pager
```

修改部署前备份应用文件、systemd 配置、crontab 和 JSON 数据。不要将 `.publish_token`、`.browser_publish_token`、`.generator.env`、运行状态或数据备份提交到 GitHub。
