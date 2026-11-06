import os, json, time, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2.service_account import Credentials

API_VERSION = "v25.0"     # phien ban Marketing API
FIRST_RUN_DAYS = 365      # lan dau: lay bao nhieu ngay
REFRESH_DAYS = 7          # cac lan sau: cap nhat lai bao nhieu ngay
CHUNK_DAYS = 30           # chia nho moi lan goi API
TAB_NAME = "FB_Ads"
HEADER = ["date", "campaign_id", "campaign", "spend",
          "impressions", "clicks", "purchases", "revenue"]

TOKEN = os.environ["FB_ACCESS_TOKEN"].strip()
ACCOUNT = os.environ["FB_AD_ACCOUNT_ID"].strip().replace("act_", "")
BASE = f"https://graph.facebook.com/{API_VERSION}"
TRANSIENT_CODES = (1, 2, 4, 17, 32, 341, 613)


def get(url, retries=4):
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
                wait = 20 * attempt
                print(f"Loi tam thoi (code {code}), thu lai sau {wait}s ({attempt}/{retries})")
                time.sleep(wait)
                continue
            print("LOI FACEBOOK API:", e.code, body)
            raise SystemExit(1)
        except (urllib.error.URLError, TimeoutError):
            if attempt < retries:
                wait = 20 * attempt
                print(f"Loi mang, thu lai sau {wait}s ({attempt}/{retries})")
                time.sleep(wait)
                continue
            print("LOI: khong ket noi duoc Facebook")
            raise SystemExit(1)


def pick_action(items, types):
    for t in types:
        for a in items or []:
            if a.get("action_type") == t:
                return float(a.get("value", 0))
    return 0.0


PURCHASE_TYPES = ["omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase"]

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

existing = [r for r in ws.get_all_values(value_render_option="UNFORMATTED_VALUE")[1:]
            if r and str(r[0]).strip() != ""]

# ---- 2. Xac dinh khoang ngay can lay ----
today = datetime.now(timezone(timedelta(hours=7))).date()
days = REFRESH_DAYS if existing else FIRST_RUN_DAYS
since = today - timedelta(days=days)
print(f"Lay du lieu tu {since} den {today} ({'cap nhat' if existing else 'lan dau'})")

# ---- 3. Lay du lieu Facebook, chia tung dot ----
new_rows = []
start = since
while start <= today:
    end = min(start + timedelta(days=CHUNK_DAYS - 1), today)
    params = urllib.parse.urlencode({
        "level": "campaign",
        "time_increment": 1,
        "time_range": json.dumps({"since": str(start), "until": str(end)}),
        "fields": "campaign_id,campaign_name,spend,impressions,clicks,actions,action_values",
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
                float(r.get("spend", 0)),
                int(r.get("impressions", 0)),
                int(r.get("clicks", 0)),
                pick_action(r.get("actions"), PURCHASE_TYPES),
                pick_action(r.get("action_values"), PURCHASE_TYPES),
            ])
            count += 1
        url = page.get("paging", {}).get("next")
    print(f"  {start} -> {end}: {count} dong")
    start = end + timedelta(days=1)
    time.sleep(1)

# ---- 4. Gop: giu du lieu cu, thay phan trong khoang vua lay ----
keep = [r[:8] for r in existing if str(r[0]) < str(since)]
all_rows = sorted(keep + new_rows, key=lambda r: (str(r[0]), str(r[1])))

# ---- 5. Ghi vao Sheet ----
if ws.row_values(1) != HEADER:
    ws.update(range_name="A1", values=[HEADER], value_input_option="RAW")
ws.batch_clear(["A2:H"])
if all_rows:
    ws.update(range_name="A2", values=all_rows, value_input_option="RAW")

print(f"Xong: lay moi {len(new_rows)} dong, tong cong {len(all_rows)} dong trong tab {TAB_NAME}")
