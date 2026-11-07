import os, json, time, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2.service_account import Credentials

API_VERSION = "v25.0"     # phien ban Marketing API
FIRST_RUN_DAYS = 365      # lan dau: lay bao nhieu ngay
REFRESH_DAYS = 7          # cac lan sau: cap nhat lai bao nhieu ngay
CHUNK_DAYS = 10           # chia nho moi lan goi API (cap ad nang hon cap camp)
TAB_NAME = "FB_Ads"
HEADER = ["date", "campaign_id", "campaign", "adset_id", "adset", "ad_id", "ad",
          "spend", "reach", "impressions", "link_clicks", "thruplay",
          "messages", "comments", "purchases", "revenue"]
NCOL = len(HEADER)

TOKEN = os.environ["FB_ACCESS_TOKEN"].strip()
ACCOUNT = os.environ["FB_AD_ACCOUNT_ID"].strip().replace("act_", "")
BASE = f"https://graph.facebook.com/{API_VERSION}"
TRANSIENT_CODES = (1, 2, 4, 17, 32, 341, 613, 80000, 80003, 80004)


def get(url, retries=5):
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore").replace(TOKEN, "***")
            try:
                code = json.loads(body).get("error", {}).get("code")
            except Exception:
                code = None
            if attempt < retries and (e.code >= 500 or code in TRANSIENT_CODES):
                wait = 30 * attempt
                print(f"Loi tam thoi (code {code}), thu lai sau {wait}s ({attempt}/{retries})")
                time.sleep(wait)
                continue
            print("LOI FACEBOOK API:", e.code, body)
            raise SystemExit(1)
        except (urllib.error.URLError, TimeoutError):
            if attempt < retries:
                wait = 30 * attempt
                print(f"Loi mang, thu lai sau {wait}s ({attempt}/{retries})")
                time.sleep(wait)
                continue
            print("LOI: khong ket noi duoc Facebook")
            raise SystemExit(1)


def pick_action(items, types):
    """Lay gia tri cua loai hanh dong dau tien tim thay (tranh dem trung)."""
    for t in types:
        for a in items or []:
            if a.get("action_type") == t:
                return float(a.get("value", 0))
    return 0.0


def sum_actions(items):
    return sum(float(a.get("value", 0)) for a in items or [])


PURCHASE_TYPES = ["omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase"]
MESSAGE_TYPES = ["onsite_conversion.messaging_conversation_started_7d"]
COMMENT_TYPES = ["comment"]

# ---- 1. Mo Google Sheet ----
creds = Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
    scopes=["https://www.googleapis.com/auth/spreadsheets"],
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(os.environ["GOOGLE_SHEET_ID"].strip())
try:
    ws = sh.worksheet(TAB_NAME)
except gspread.WorksheetNotFound:
    print(f"LOI: khong thay tab '{TAB_NAME}' trong Google Sheet")
    raise SystemExit(1)

header_now = ws.row_values(1)
if header_now != HEADER:
    if header_now:
        print("Tieu de cot da doi: se xoa tab va tai lai toan bo du lieu")
    ws.clear()
    existing = []
else:
    existing = [r for r in ws.get_all_values(value_render_option="UNFORMATTED_VALUE")[1:]
                if r and str(r[0]).strip() != ""]

# ---- 2. Xac dinh khoang ngay can lay ----
today = datetime.now(timezone(timedelta(hours=7))).date()
days = REFRESH_DAYS if existing else FIRST_RUN_DAYS
since = today - timedelta(days=days)
print(f"Lay du lieu tu {since} den {today} ({'cap nhat' if existing else 'lan dau'})")

# ---- 3. Lay du lieu Facebook cap ad, chia tung dot ----
FIELDS = ("campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,"
          "spend,reach,impressions,inline_link_clicks,"
          "actions,action_values,video_thruplay_watched_actions")
new_rows = []
start = since
while start <= today:
    end = min(start + timedelta(days=CHUNK_DAYS - 1), today)
    params = urllib.parse.urlencode({
        "level": "ad",
        "time_increment": 1,
        "time_range": json.dumps({"since": str(start), "until": str(end)}),
        "fields": FIELDS,
        "limit": 200,
        "access_token": TOKEN,
    })
    url = f"{BASE}/act_{ACCOUNT}/insights?{params}"
    count = 0
    while url:
        page = get(url)
        for r in page.get("data", []):
            new_rows.append([
                r["date_start"],
                str(r.get("campaign_id", "")),
                r.get("campaign_name", ""),
                str(r.get("adset_id", "")),
                r.get("adset_name", ""),
                str(r.get("ad_id", "")),
                r.get("ad_name", ""),
                float(r.get("spend", 0)),
                int(r.get("reach", 0)),
                int(r.get("impressions", 0)),
                int(r.get("inline_link_clicks", 0)),
                sum_actions(r.get("video_thruplay_watched_actions")),
                pick_action(r.get("actions"), MESSAGE_TYPES),
                pick_action(r.get("actions"), COMMENT_TYPES),
                pick_action(r.get("actions"), PURCHASE_TYPES),
                pick_action(r.get("action_values"), PURCHASE_TYPES),
            ])
            count += 1
        url = page.get("paging", {}).get("next")
    print(f"  {start} -> {end}: {count} dong")
    start = end + timedelta(days=1)
    time.sleep(1)

# ---- 4. Gop: giu du lieu cu, thay phan trong khoang vua lay ----
keep = [r[:NCOL] for r in existing if str(r[0]) < str(since)]
all_rows = sorted(keep + new_rows, key=lambda r: (str(r[0]), str(r[5])))

# ---- 5. Ghi vao Sheet (chia lo de khong qua nang) ----
ws.resize(rows=max(len(all_rows) + 100, 1000), cols=NCOL)
ws.update(range_name="A1", values=[HEADER], value_input_option="RAW")
ws.batch_clear([f"A2:{chr(64 + NCOL)}"])
BATCH = 5000
for i in range(0, len(all_rows), BATCH):
    ws.update(range_name=f"A{i + 2}", values=all_rows[i:i + BATCH],
              value_input_option="RAW")

print(f"Xong: lay moi {len(new_rows)} dong, tong cong {len(all_rows)} dong trong tab {TAB_NAME}")


# ================= XUAT data.json CHO TRANG BAO CAO =================
from datetime import date


def to_iso_date(v):
    if isinstance(v, (int, float)) and v > 30000:      # ngay dang so cua Google Sheet
        return str(date(1899, 12, 30) + timedelta(days=int(v)))
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def n(x):
    return int(x) if isinstance(x, float) and x.is_integer() else x


names = {"campaign": {}, "adset": {}, "ad": {}}
fb_rows = []
for r in all_rows:   # da sap xep theo ngay, ten moi nhat se ghi de ten cu
    names["campaign"][r[1]] = r[2]
    names["adset"][r[3]] = r[4]
    names["ad"][r[5]] = r[6]
    fb_rows.append([r[0], r[1], r[3], r[5]] + [n(r[i]) for i in range(7, 16)])

rev_cols, rev_rows = [], []
try:
    rv = sh.worksheet("Doanh_thu").get_all_values(value_render_option="UNFORMATTED_VALUE")
except gspread.WorksheetNotFound:
    rv = []
    print("Canh bao: khong thay tab Doanh_thu dau ca???")
if rv:
    rev_cols = [str(c).strip() for c in rv[0]]
    for r in rv[1:]:
        if not r or str(r[0]).strip() == "":
            continue
        d = to_iso_date(r[0])
        if not d:
            print("Bo qua dong doanh thu co ngay khong doc duoc:", r[0])
            continue
        row = [d] + [n(c) if isinstance(c, float) else c for c in r[1:len(rev_cols)]]
        row += [""] * (len(rev_cols) - len(row))
        rev_rows.append(row)

out = {
    "generated_at": datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%d %H:%M"),
    "fb": {
        "cols": ["date", "campaign_id", "adset_id", "ad_id", "spend", "reach", "impressions",
                 "link_clicks", "thruplay", "messages", "comments", "purchases", "revenue"],
        "rows": fb_rows,
    },
    "names": names,
    "revenue": {"cols": rev_cols, "rows": rev_rows},
}
with open("data.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
print(f"Da xuat data.json: {len(fb_rows)} dong quang cao, {len(rev_rows)} dong doanh thu")
