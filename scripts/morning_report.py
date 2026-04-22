import os
import html
import base64
import re
import requests
import anthropic
from datetime import datetime, timedelta, timezone
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from bs4 import BeautifulSoup

DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK"]
CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
YOUTUBE_REFRESH_TOKEN = os.environ["YOUTUBE_REFRESH_TOKEN"]
GMAIL_REFRESH_TOKEN = os.environ["GMAIL_REFRESH_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

TOKEN_URI = "https://oauth2.googleapis.com/token"
TW_TZ = timezone(timedelta(hours=8))

now = datetime.now(timezone.utc)
today_str = now.astimezone(TW_TZ).strftime("%Y-%m-%d")
yesterday_start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
yesterday_date_str = yesterday_start.strftime("%Y/%m/%d")


def get_youtube_creds():
    return Credentials(
        token=None,
        refresh_token=YOUTUBE_REFRESH_TOKEN,
        token_uri=TOKEN_URI,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/youtube.force-ssl",
                "https://www.googleapis.com/auth/youtube.readonly"],
    )


def get_gmail_creds():
    return Credentials(
        token=None,
        refresh_token=GMAIL_REFRESH_TOKEN,
        token_uri=TOKEN_URI,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=["https://mail.google.com/",
                "https://www.googleapis.com/auth/calendar"],
    )


def fetch_videos():
    creds = get_youtube_creds()
    yt = build("youtube", "v3", credentials=creds)
    channel_res = yt.channels().list(part="contentDetails", mine=True).execute()
    uploads_id = channel_res["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    playlist_res = yt.playlistItems().list(
        part="contentDetails",
        playlistId=uploads_id,
        maxResults=10,
    ).execute()
    video_ids = [item["contentDetails"]["videoId"] for item in playlist_res.get("items", [])]
    if not video_ids:
        return []
    detail_res = yt.videos().list(
        part="snippet,statistics",
        id=",".join(video_ids),
    ).execute()
    return detail_res.get("items", [])


def fetch_comments(video_id):
    creds = get_youtube_creds()
    yt = build("youtube", "v3", credentials=creds)
    try:
        res = yt.commentThreads().list(
            part="snippet",
            videoId=video_id,
            order="time",
            maxResults=20,
        ).execute()
    except Exception:
        return []
    comments = []
    for item in res.get("items", []):
        top = item["snippet"]["topLevelComment"]["snippet"]
        published = datetime.fromisoformat(top["publishedAt"].replace("Z", "+00:00"))
        if published >= yesterday_start:
            comments.append({
                "author": top["authorDisplayName"],
                "text": top["textDisplay"][:100],
                "likes": top["likeCount"],
                "time": top["publishedAt"][:16],
                "draft_reply": "",
            })
    return comments


def draft_reply_for_comment(comment_text, author):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = f"""你是 YouTube 頻道 LYC 的頻道主，請根據以下留言，用**與留言相同的語言**草擬一則自然、友善的簡短回覆（不超過 30 字）。只輸出回覆內容，不要加任何說明。

作者：{author}
留言：{comment_text}"""
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=80,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def extract_body(payload):
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore") if data else ""
    if payload.get("mimeType") == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            html_content = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
            return BeautifulSoup(html_content, "html.parser").get_text(separator="\n")
    for part in payload.get("parts", []):
        result = extract_body(part)
        if result:
            return result
    return ""


def summarize_emails(emails):
    if not emails:
        return []
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    summaries = []
    for e in emails:
        body_preview = e["body"][:1500]
        prompt = f"""以下是一封郵件，請用一句繁體中文摘要它的重點（不超過 40 字）：

寄件人：{e['from']}
主旨：{e['subject']}
內容：
{body_preview}"""
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        summaries.append({
            "from": e["from"],
            "subject": e["subject"],
            "summary": msg.content[0].text.strip(),
        })
    return summaries


def fetch_gmail():
    creds = get_gmail_creds()
    gmail = build("gmail", "v1", credentials=creds)
    query = f"is:unread after:{yesterday_date_str}"
    res = gmail.users().messages().list(userId="me", q=query, maxResults=10).execute()
    messages = []
    for msg in res.get("messages", []):
        detail = gmail.users().messages().get(userId="me", id=msg["id"], format="full").execute()
        headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
        body = extract_body(detail["payload"])
        messages.append({
            "from": headers.get("From", ""),
            "subject": headers.get("Subject", ""),
            "body": body,
        })
    return messages


def fetch_calendar():
    creds = get_gmail_creds()
    cal = build("calendar", "v3", credentials=creds)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    today_end = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()
    res = cal.events().list(
        calendarId="primary",
        timeMin=today_start,
        timeMax=today_end,
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    events = []
    for e in res.get("items", []):
        start = e["start"].get("dateTime", e["start"].get("date", ""))
        events.append({"title": e.get("summary", "（無標題）"), "start": start[:16]})
    return events


def send_discord(content):
    resp = requests.post(DISCORD_WEBHOOK, json={"content": content})
    return resp.status_code


def generate_html(videos, all_comments, events, report_date):
    # Top 5 by views
    top5 = sorted(videos, key=lambda v: int(v["statistics"].get("viewCount", 0)), reverse=True)[:5]

    # Video cards
    video_cards_html = ""
    for v in top5:
        s = v["snippet"]
        st = v["statistics"]
        thumb = s["thumbnails"].get("medium", s["thumbnails"].get("default", {})).get("url", "")
        title = html.escape(s["title"])
        views = f"{int(st.get('viewCount', 0)):,}"
        likes = f"{int(st.get('likeCount', 0)):,}"
        comment_count = f"{int(st.get('commentCount', 0)):,}"
        video_cards_html += f"""
        <div class="bg-gray-50 rounded-xl overflow-hidden shadow-sm hover:shadow-md transition-shadow">
            <img src="{thumb}" alt="{title}" class="w-full aspect-video object-cover">
            <div class="p-3">
                <p class="text-sm font-medium text-gray-800 line-clamp-2 mb-2">{title}</p>
                <div class="flex gap-3 text-xs text-gray-500">
                    <span>👁 {views}</span>
                    <span>👍 {likes}</span>
                    <span>💬 {comment_count}</span>
                </div>
            </div>
        </div>"""

    # Comments
    comments_html = ""
    has_new_comments = False
    for v in videos:
        vid = v["id"]
        comments = all_comments.get(vid, [])
        if not comments:
            continue
        has_new_comments = True
        video_title = html.escape(v["snippet"]["title"])
        comments_html += f'<div class="mb-6"><p class="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3 truncate">{video_title}</p>'
        for c in comments:
            author = html.escape(c["author"])
            text = html.escape(c["text"])
            draft = html.escape(c.get("draft_reply", ""))
            draft_html = ""
            if draft:
                draft_html = f"""
                <div class="mt-2 bg-indigo-50 border-l-4 border-indigo-300 pl-3 py-2 rounded-r-lg">
                    <span class="text-xs text-indigo-400 font-medium">📝 草擬回覆</span>
                    <p class="text-sm text-indigo-700 mt-0.5">{draft}</p>
                </div>"""
            comments_html += f"""
            <div class="mb-4 bg-white border border-gray-100 rounded-lg p-4 shadow-sm">
                <div class="flex items-center gap-2 mb-1 flex-wrap">
                    <span class="font-medium text-sm text-gray-800">{author}</span>
                    <span class="text-xs text-gray-400">👍 {c['likes']}</span>
                    <span class="text-xs text-gray-400">{c['time']}</span>
                </div>
                <p class="text-sm text-gray-700">「{text}」</p>
                {draft_html}
            </div>"""
        comments_html += "</div>"

    if not has_new_comments:
        comments_html = '<p class="text-gray-400 text-sm">今日無新留言</p>'

    # Calendar
    calendar_html = ""
    if events:
        for e in events:
            start = e["start"]
            time_str = start[11:16] if "T" in start else "全天"
            title_escaped = html.escape(e["title"])
            calendar_html += f"""
            <div class="flex items-center gap-4 py-2.5 border-b border-gray-100 last:border-0">
                <span class="text-indigo-500 font-mono text-sm w-12 shrink-0">{time_str}</span>
                <span class="text-sm text-gray-700">{title_escaped}</span>
            </div>"""
    else:
        calendar_html = '<p class="text-gray-400 text-sm">今日無行程</p>'

    tw_time = datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M")

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LYC 早報｜{report_date}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        .line-clamp-2 {{
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }}
    </style>
</head>
<body class="bg-white text-gray-900 font-sans">
    <div class="max-w-4xl mx-auto px-4 py-8">

        <div class="mb-8">
            <h1 class="text-2xl font-bold text-indigo-600">📋 LYC 早報</h1>
            <p class="text-gray-400 text-sm mt-1">{report_date}</p>
        </div>

        <section class="mb-10">
            <h2 class="text-lg font-bold text-gray-800 mb-4">🎬 近期影片（Top 5 by 觀看數）</h2>
            <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-3">
                {video_cards_html}
            </div>
        </section>

        <section class="mb-10">
            <h2 class="text-lg font-bold text-gray-800 mb-4">💬 新留言（昨天之後）</h2>
            {comments_html}
        </section>

        <section class="mb-10">
            <h2 class="text-lg font-bold text-gray-800 mb-4">📅 今日行事曆</h2>
            <div class="bg-gray-50 rounded-xl p-4">
                {calendar_html}
            </div>
        </section>

        <footer class="text-xs text-gray-400 text-center border-t pt-4">
            最後更新：{tw_time} 台灣時間
        </footer>

    </div>
</body>
</html>"""


def main():
    # Fetch data
    videos = fetch_videos()
    all_comments = {}
    for v in videos:
        vid = v["id"]
        count = int(v["statistics"].get("commentCount", 0))
        if count > 0:
            comments = fetch_comments(vid)
            for c in comments:
                c["draft_reply"] = draft_reply_for_comment(c["text"], c["author"])
            all_comments[vid] = comments
        else:
            all_comments[vid] = []

    emails = fetch_gmail()
    email_summaries = summarize_emails(emails)
    events = fetch_calendar()

    # Generate HTML
    page_html = generate_html(videos, all_comments, events, today_str)
    os.makedirs("public", exist_ok=True)
    with open("public/index.html", "w", encoding="utf-8") as f:
        f.write(page_html)
    print("index.html 已產生")

    # Build Discord message 1: videos + comments
    lines1 = [f"## 📋 LYC 早報｜{today_str}", "", "### 🎬 近五天影片"]
    for v in videos:
        s = v["snippet"]
        st = v["statistics"]
        lines1.append(f"- **{s['title']}** | 觀看 {st.get('viewCount','?')} | 讚 {st.get('likeCount','?')} | 留言 {st.get('commentCount','?')}")

    lines1.append("")
    lines1.append("### 💬 新留言（昨天之後）")
    has_comments = False
    for v in videos:
        vid = v["id"]
        comments = all_comments.get(vid, [])
        if comments:
            has_comments = True
            lines1.append(f"**{v['snippet']['title']}**")
            for c in comments:
                lines1.append(f"- {c['author']}：{c['text']} ({c['time']})")
    if not has_comments:
        lines1.append("今日無新留言")

    msg1 = "\n".join(lines1)[:1900]

    # Build Discord message 2: gmail + calendar
    lines2 = ["### 📧 Gmail 未讀信件"]
    if email_summaries:
        for e in email_summaries:
            sender = re.sub(r"<.*?>", "", e["from"]).strip()
            lines2.append(f"- **{e['subject']}**（{sender}）")
            lines2.append(f"  {e['summary']}")
    else:
        lines2.append("無新信件")

    lines2.append("")
    lines2.append("### 📅 今日行事曆")
    if events:
        for e in events:
            lines2.append(f"- {e['start']} {e['title']}")
    else:
        lines2.append("今日無行程")

    msg2 = "\n".join(lines2)[:1900]

    # Send Discord
    code1 = send_discord(msg1)
    code2 = send_discord(msg2)
    print(f"Discord: {code1}, {code2}")
    if code1 == 204 and code2 == 204:
        print("早報推播成功")
    else:
        print("推播失敗，請檢查 webhook")
        exit(1)


if __name__ == "__main__":
    main()
