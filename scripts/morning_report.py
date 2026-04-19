import os
import json
import requests
from datetime import datetime, timedelta, timezone
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK"]
CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
YOUTUBE_REFRESH_TOKEN = os.environ["YOUTUBE_REFRESH_TOKEN"]
GMAIL_REFRESH_TOKEN = os.environ["GMAIL_REFRESH_TOKEN"]

TOKEN_URI = "https://oauth2.googleapis.com/token"

now = datetime.now(timezone.utc)
today_str = now.strftime("%Y-%m-%d")
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
    res = yt.videos().list(
        part="snippet,statistics",
        mine=True,
        myRating=None,
        maxResults=10,
    )
    # Use search instead
    search_res = yt.search().list(
        part="id",
        forMine=True,
        type="video",
        order="date",
        maxResults=10,
    ).execute()
    video_ids = [item["id"]["videoId"] for item in search_res.get("items", [])]
    if not video_ids:
        return []
    detail_res = yt.videos().list(
        part="snippet,statistics",
        id=",".join(video_ids),
    ).execute()
    videos = detail_res.get("items", [])
    videos.sort(key=lambda v: int(v["statistics"].get("viewCount", 0)), reverse=True)
    return videos[:5]


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
            })
    return comments


def fetch_gmail():
    creds = get_gmail_creds()
    gmail = build("gmail", "v1", credentials=creds)
    query = f"is:unread after:{yesterday_date_str}"
    res = gmail.users().messages().list(userId="me", q=query, maxResults=10).execute()
    messages = []
    for msg in res.get("messages", []):
        detail = gmail.users().messages().get(userId="me", id=msg["id"], format="metadata",
                                               metadataHeaders=["From", "Subject", "Date"]).execute()
        headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
        messages.append({
            "from": headers.get("From", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
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


def main():
    # Fetch data
    videos = fetch_videos()
    all_comments = {}
    for v in videos:
        vid = v["id"]
        all_comments[vid] = fetch_comments(vid)

    emails = fetch_gmail()
    events = fetch_calendar()

    # Build message 1: videos + comments
    lines1 = [f"## 📋 LYC 早報｜{today_str}", "", "### 🎬 近期影片（Top 5 by 觀看數）"]
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

    # Build message 2: gmail + calendar
    lines2 = ["### 📧 Gmail 重要信件"]
    if emails:
        for e in emails:
            lines2.append(f"- **{e['subject']}** | {e['from'][:30]} | {e['date'][:16]}")
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

    # Send
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
