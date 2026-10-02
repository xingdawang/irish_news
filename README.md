# Irish News

爱尔兰新闻简报网站与发布 API。默认监听端口 **5310**。

## API
- POST /api/news/publish：发布/更新一期新闻（Bearer Token）
- GET /api/news/latest：最新一期
- GET /api/news/editions：历史期数
- GET /api/news/editions/<YYYY-MM-DD-HHMM>：指定一期
- GET /health：健康检查

## 环境变量
IRELAND_NEWS_PUBLISH_TOKEN：高强度随机密钥（不要提交 GitHub）
PORT=5310
NEWS_DATA_DIR=/var/lib/irish-news

## 运行
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
IRELAND_NEWS_PUBLISH_TOKEN=test-token python app.py

同一个 slug 可以安全重试，只更新同一期。只有发布 API 成功后 updated_at 才会变化，所以“超过18小时未更新”反映真实发布状态。生产环境应通过 HTTPS 暴露发布接口。
