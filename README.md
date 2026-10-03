# Irish News

爱尔兰新闻简报网站。当前架构是 **JSON-first**：网站不负责生成新闻，只负责接收、校验、存档和展示标准 JSON。

## 架构

```
新闻生成端
   ↓ 标准 JSON
POST /api/news/publish
   ↓
json_data/editions/<slug>.json
   ↓
FastAPI 页面与读取 API
```

运行时的正式内容源是 `json_data/`，不再依赖 SQLite 才能展示。旧 `news.db` 保留作为历史迁移和回滚备份。

## JSON 格式

每一期使用固定结构：

```json
{
  "version": 1,
  "slug": "2026-10-03-1800",
  "title": "🇮🇪 爱尔兰新闻简报｜2026-10-03 18:00｜住房 & IT 优先",
  "scheduled_at": "2026-10-03T18:00:00+01:00",
  "generated_at": "2026-10-03T18:05:00+01:00",
  "cutoff_at": "2026-10-03T18:05:00+01:00",
  "items": [
    {
      "event_key": "stable-event-key",
      "category": "housing",
      "region": "Dublin",
      "title": "中文标题",
      "summary": "1–2 句中文摘要",
      "source_name": "Source",
      "source_url": "https://example.com/exact-article",
      "published_at": "2026-10-03T16:30:00+01:00",
      "is_update": false
    }
  ]
}
```

只有同一真实事件出现明确新事实、新决定、新数据或新阶段时，才允许再次出现。此时沿用原 `event_key`，设置 `is_update=true`，并增加 `update_note`。

## 网站功能

现有功能全部保留：

- 最新一期
- 住房 / IT / 其他分类筛选
- 历史简报
- 今天 / 最近 3 天 / 最近 7 天 / 全部
- 上一期 / 下一期
- 浏览器本地已读 / 未读标记
- 原文详情页链接
- 实际发布时间和 stale 提示
- 跨期 event_key / URL 去重
- 同一期幂等重放

## 写入 API

`POST /api/news/publish`

Headers:

```
Authorization: Bearer <publish token>
Content-Type: application/json
```

发布 Token 只保存在服务器：

```
/home/ubuntu/ireland-news/.publish_token
```

Token 不进入 GitHub。

同一个 slug：

- 完全相同 JSON 重放：返回 `status=exists`
- 内容不同：HTTP 409
- 跨期重复事件但没有 `is_update=true`：HTTP 409

## 读取 API

- `GET /health`
- `GET /api/latest`
- `GET /api/editions`
- `GET /api/editions/<slug>`
- `GET /api/events/recent?days=14`

## 数据目录

```
json_data/
├── index.json
└── editions/
    ├── 2026-10-03-0000.json
    ├── 2026-10-03-0600.json
    └── ...
```

每一期 JSON 是独立、可读、可备份、可回滚的文件。写入使用文件锁和原子替换，避免半写入文件。

## 从旧 SQLite 迁移

```bash
./.venv/bin/python migrate_sqlite_to_json.py \
  --db news.db \
  --out json_data
```

迁移不会删除或修改原 `news.db`。

## 本地启动

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8200
```

默认外部入口仍由 Nginx 的 5310 反向代理到 127.0.0.1:8200。

## 备份

`backup_db.py` 同时备份：

- 旧 SQLite：`news-*.db.gz`
- JSON 数据：`json-news-*.tar.gz`

默认保留 60 天。

## 设计原则

新闻生成端和网站完全解耦。网站不需要 LLM API Key，也不关心 JSON 是由 ChatGPT、其他模型、脚本还是人工生成；只要 JSON 符合 schema，网站就能解析、存档并保持现有展示功能。
