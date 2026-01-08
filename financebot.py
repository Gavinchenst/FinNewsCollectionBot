# 福生无量天尊
from openai import OpenAI
import feedparser
import requests
from newspaper import Article
from datetime import datetime
import time
import pytz
import os
import traceback

# OpenAI API Key
openai_api_key = os.getenv("OPENAI_API_KEY")
# 从环境变量获取 Server酱 SendKeys
server_chan_keys_env = os.getenv("SERVER_CHAN_KEYS")
if not server_chan_keys_env:
    raise ValueError("环境变量 SERVER_CHAN_KEYS 未设置，请在Github Actions中设置此变量！")
server_chan_keys = server_chan_keys_env.split(",")

openai_client = OpenAI(api_key=openai_api_key, base_url="https://api.deepseek.com/v1")

# RSS源地址列表
rss_feeds = {
    "💲 华尔街见闻":{
        "华尔街见闻":"https://dedicated.wallstreetcn.com/rss.xml",      
    },
    "💻 36氪":{
        "36氪":"https://36kr.com/feed",   
        },
    "🇨🇳 中国经济": {
        "香港經濟日報":"https://www.hket.com/rss/china",
        "东方财富":"http://rss.eastmoney.com/rss_partener.xml",
        "百度股票焦点":"http://news.baidu.com/n?cmd=1&class=stock&tn=rss&sub=0",
        "中新网":"https://www.chinanews.com.cn/rss/finance.xml",
        "国家统计局-最新发布":"https://www.stats.gov.cn/sj/zxfb/rss.xml",
    },
      "🇺🇸 美国经济": {
        "华尔街日报 - 经济":"https://feeds.content.dowjones.io/public/rss/WSJcomUSBusiness",
        "华尔街日报 - 市场":"https://feeds.content.dowjones.io/public/rss/RSSMarketsMain",
        "MarketWatch美股": "https://www.marketwatch.com/rss/topstories",
        "ZeroHedge华尔街新闻": "https://feeds.feedburner.com/zerohedge/feed",
        "ETF Trends": "https://www.etftrends.com/feed/",
    },
    "🌍 世界经济": {
        "华尔街日报 - 经济":"https://feeds.content.dowjones.io/public/rss/socialeconomyfeed",
        "BBC全球经济": "http://feeds.bbci.co.uk/news/business/rss.xml",
    },
}

# 配置常量
TIMEOUT_SECONDS = 10  # 每篇文章请求超时 10s
REQUEST_RETRIES = 3   # RSS 请求与网络请求的短重试次数
REQUEST_RETRY_DELAY = 2  # 重试间隔（秒）
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


# 获取北京时间
def today_date():
    return datetime.now(pytz.timezone("Asia/Shanghai")).date()

def safe_referer_from_url(url: str) -> str:
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}/"
    except Exception:
        pass
    return ""

# 爬取网页正文 (用于 AI 分析，但不展示)
# 修改说明：
# - 使用 requests.get(..., timeout=TIMEOUT_SECONDS) 获取 HTML，避免 newspaper.download() 的内部网络调用无超时控制
# - 返回 (text, error)，text 允许为空；error 为 None 表示成功，否则为错误描述字符串
def fetch_article_text(url):
    headers = {
        'User-Agent': USER_AGENT,
        'Referer': safe_referer_from_url(url),
        'Accept-Language': 'zh-CN,zh;q=0.9,en-US,en;q=0.8'
    }

    # 简单重试逻辑（针对瞬时网络错误）
    last_err = None
    for attempt in range(REQUEST_RETRIES):
        try:
            print(f"📰 正在爬取文章内容: {url} (attempt {attempt+1})")
            resp = requests.get(url, headers=headers, timeout=TIMEOUT_SECONDS)
            status = resp.status_code
            if status != 200:
                # 403/401/其他视为失败，返回空文本并记录错误
                err_msg = f"HTTP_{status}"
                print(f"❌ HTTP 状态非200: {status}，URL: {url}")
                return "", err_msg
            html = resp.text or ""
            if not html.strip():
                print(f"⚠️ 文章HTML为空: {url}")
                # 返回空文本，但不算错误
                return "", None
            # 用 newspaper 解析 HTML（避免再次发起网络请求）
            try:
                article = Article(url)
                article.set_html(html)
                article.parse()
                text = (article.text or "")[:1500]  # 限制长度，防止超出 API 输入限制
                if not text:
                    print(f"⚠️ 文章解析后正文为空: {url}")
                return text, None
            except Exception as parse_exc:
                # 解析失败：记录错误，但返回空文本以便继续整体流程
                err = f"parse_error: {parse_exc}"
                print(f"❌ 解析文章失败: {url}, 错误: {parse_exc}")
                return "", err
        except requests.Timeout:
            # 超时立即视为该文章爬取失败并跳过（根据需求）
            print(f"❌ 超时 ({TIMEOUT_SECONDS}s) 在 URL: {url}")
            return "", "timeout"
        except requests.RequestException as req_exc:
            last_err = req_exc
            print(f"⚠️ 请求异常: {req_exc}，URL: {url}，{REQUEST_RETRIES-attempt-1} 次重试剩余")
            time.sleep(REQUEST_RETRY_DELAY)
            continue
        except Exception as e:
            print(f"❌ 未知错误在爬取 URL {url}: {traceback.format_exc()}")
            return "", f"unknown_error: {e}"
    # 如果所有重试都失败
    return "", f"request_exception: {last_err}"

# 添加 User-Agent 头
def fetch_feed_with_headers(url):
    headers = {
        'User-Agent': USER_AGENT
    }
    # feedparser.parse 支持 request_headers 参数
    return feedparser.parse(url, request_headers=headers)


# 自动重试获取 RSS
def fetch_feed_with_retry(url, retries=3, delay=5):
    for i in range(retries):
        try:
            feed = fetch_feed_with_headers(url)
            if feed and hasattr(feed, 'entries') and len(feed.entries) > 0:
                return feed
            else:
                print(f"⚠️ 第 {i+1} 次请求 {url} 未获取到条目")
        except Exception as e:
            print(f"⚠️ 第 {i+1} 次请求 {url} 失败: {e}")
        time.sleep(delay)
    print(f"❌ 跳过 {url}, 尝试 {retries} 次后仍失败。")
    return None

# 获取RSS内容（爬取正文但不展示）
# 修改：收集 failures 列表；fetch_article_text 返回 (text, error)；即便存在错误也继续处理已爬取内容
def fetch_rss_articles(rss_feeds, max_articles=10):
    news_data = {}
    analysis_text = ""  # 用于AI分析的正文内容
    failures = []  # 记录爬取失败的条目信息

    for category, sources in rss_feeds.items():
        category_content = ""
        for source, url in sources.items():
            print(f"📡 正在获取 {source} 的 RSS 源: {url}")
            feed = fetch_feed_with_retry(url)
            if not feed:
                print(f"⚠️ 无法获取 {source} 的 RSS 数据")
                failures.append({"source": source, "url": url, "error": "rss_fetch_failed"})
                continue
            print(f"✅ {source} RSS 获取成功，共 {len(feed.entries)} 条新闻")

            articles = []  # 每个 source 都需要重新初始化列表
            count = 0
            for entry in feed.entries:
                if count >= max_articles:
                    break
                title = entry.get('title', '无标题')
                link = entry.get('link', '') or entry.get('guid', '')
                if not link:
                    print(f"⚠️ {source} 的新闻 '{title}' 没有链接，跳过")
                    failures.append({"source": source, "title": title, "url": "", "error": "no_link"})
                    continue

                # 爬取正文用于分析（不展示）
                text, err = fetch_article_text(link)
                if err is None:
                    # 成功或解析为空但没有错误
                    if text:
                        analysis_text += f"【{title}】\n{text}\n\n"
                else:
                    # 记录失败信息；如果 text 里有内容（例如解析部分成功），仍加入分析
                    failures.append({"source": source, "title": title, "url": link, "error": err})
                    if text:
                        analysis_text += f"【{title}】\n{text}\n\n"

                print(f"🔹 {source} - {title} 处理完毕 (url: {link})")
                articles.append(f"- [{title}]({link})")
                count += 1

            if articles:
                category_content += f"### {source}\n" + "\n".join(articles) + "\n\n"

        news_data[category] = category_content

    return news_data, analysis_text, failures

# AI 生成内容摘要（基于爬取的正文）
def summarize(text):
    # 若没有正文可以分析，返回占位文本并不调用API（避免调用空内容）
    if not text or not text.strip():
        return "（未能获取到足够的正文用于自动分析，请查看下方的爬取结果与失败列表。）"

    try:
        completion = openai_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": """
                 你是一名专业的财经新闻分析师，请根据以下新闻内容，按照以下步骤完成任务：
                 1. 提取新闻中涉及的主要行业和主题，找出近1天涨幅最高的3个行业或主题，以及近3天涨幅较高且此前2周表现平淡的3个行业/主题。（如新闻未提供具体涨幅，请结合描述和市场情绪推测热点）
                 2. 针对每个热点，输出：
                    - 催化剂：分析近期上涨的可能原因（政策、数据、事件、情绪等）。
                    - 复盘：梳理过去3个月该行业/主题的核心逻辑、关键动态与阶段性走势。
                    - 展望：判断该热点是短期炒作还是有持续行情潜力。
                 3. 将以上分析整合为一篇1500字以内的财经热点摘要，逻辑清晰、重点突出，适合专业投资者阅读。
                 4. 根据这些信息分析相关最大受益A股个股前10位，给出股票名称，代码及利好分析。
                 """},
                {"role": "user", "content": text}
            ]
        )
        # deepseek 返回可能与 openai 不完全一致，保守处理
        # 在大多数 SDK 中，response.choices[0].message.content 可用
        choice = completion.choices[0]
        # 兼容不同 response 结构
        if hasattr(choice, "message") and hasattr(choice.message, "content"):
            return choice.message.content.strip()
        elif isinstance(choice, dict) and "message" in choice and "content" in choice["message"]:
            return choice["message"]["content"].strip()
        elif hasattr(choice, "text"):
            return choice.text.strip()
        else:
            return str(choice)
    except Exception as e:
        # 记录但不抛出，返回占位文本以便流程继续
        print(f"❌ 调用 deepseek/OpenAI 生成摘要失败: {e}\n{traceback.format_exc()}")
        return "（自动分析过程中发生错误，未能生成摘要；请查看原文链接与爬取失败列表以获取详情。）"

# 发送微信推送
def send_to_wechat(title, content):
    for key in server_chan_keys:
        url = f"https://sctapi.ftqq.com/{key}.send"
        data = {"title": title, "desp": content}
        try:
            response = requests.post(url, data=data, timeout=10)
            if response.ok:
                print(f"✅ 推送成功: {key}")
            else:
                print(f"❌ 推送失败: {key}, 响应：{response.status_code} {response.text}")
        except Exception as e:
            print(f"❌ 发送到 server 酱失败: {e}")


def send_to_feishu(webhooks, title, content):
    # webhooks: list[str]
    for url in webhooks:
        payload = {
            "msg_type": "markdown",
            "markdown": {"title": title, "text": content}
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.ok:
                print(f"✅ Feishu 推送成功: {url}")
            else:
                print(f"❌ Feishu 推送失败: {url}, 响应：{resp.status_code} {resp.text}")
        except Exception as fe:
            print(f"❌ 发送到 Feishu 失败: {fe}")


def chunk_text_by_len(text, max_len=2000):
    lines = text.splitlines(True)
    chunks = []
    cur = ""
    for line in lines:
        if len(cur) + len(line) > max_len:
            if cur:
                chunks.append(cur)
                cur = line
            else:
                for i in range(0, len(line), max_len):
                    chunks.append(line[i:i+max_len])
                cur = ""
        else:
            cur += line
    if cur:
        chunks.append(cur)
    return chunks


if __name__ == "__main__":
    today_str = today_date().strftime("%Y-%m-%d")

    # 每个网站获取最多 10 篇文章（可调整）
    articles_data, analysis_text, failures = fetch_rss_articles(rss_feeds, max_articles=10)
    
    # AI生成摘要（如果分析正文为空，summarize 会返回占位文本）
    summary = summarize(analysis_text)

    # 生成仅展示标题和链接的最终消息
    final_summary = f"📅 **{today_str} 财经新闻摘要**\n\n✍️ **今日分析总结：**\n{summary}\n\n---\n\n"
    for category, content in articles_data.items():
        if content.strip():
            final_summary += f"## {category}\n{content}\n\n"

    # 在消息尾部追加失败摘要（包含 URL + 错误信息）
    if failures:
        final_summary += "\n\n---\n\n爬取失败列表（若有多条，按顺序列出）：\n"
        for f in failures:
            src = f.get("source", "")
            title = f.get("title", "")
            url = f.get("url", "")
            err = f.get("error", "")
            if title:
                final_summary += f"- [{title}]({url}) ({src}): {err}\n"
            else:
                final_summary += f"- {url} ({src}): {err}\n"

    # 推送到多个 server 酱 key
    send_to_wechat(title=f"📌 {today_str} 财经新闻摘要", content=final_summary)

    # Feishu 推送（多 webhook）
    feishu_env = os.getenv("FEISHU_WEBHOOK_URLS")
    webhooks = []
    if feishu_env:
        webhooks = [u.strip() for u in feishu_env.split(",") if u.strip()]
    else:
        single = os.getenv("FEISHU_WEBHOOK_URL")
        if single:
            single = single.strip()
            if single:
                webhooks = [single]
    if webhooks:
        for category, content in articles_data.items():
            if not content.strip():
                continue
            full_text = f"### {category}\n{content}"
            chunks = chunk_text_by_len(full_text, max_len=2000)
            total = len(chunks)
            for idx, part in enumerate(chunks, start=1):
                title = f"📌 {today_str} 财经新闻摘要 - {category} (Part {idx}/{total})"
                send_to_feishu(webhooks, title, part)
