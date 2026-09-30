#!/usr/bin/env python3
"""Refresh one OpenRice district into data/{district}/restaurants.json.

Source: OpenRice public search and photo APIs. No login.
Covers, 餐牌, 環境, and 食物 are hotlinked. Each restaurant keeps the newest
50 photos per category in data/{district}/photos/{poiId}.json. Counts in the
restaurant file are the OpenRice totals. Google ratings are not stored.
Distance is not stored. Opening hours come from search poiHours.
Online booking is TableMap.
"""

from __future__ import annotations

import os

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import argparse
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "images"
COVERS = IMAGES / "cover"
MENUS = IMAGES / "menu"
DATA_PATH = ROOT / "data" / "tst" / "restaurants.json"
PHOTO_DIR = ROOT / "data" / "tst" / "photos"
CACHE = ROOT / "_cache"
SEARCH_CACHE = CACHE / "search"
MENU_CACHE = CACHE / "menu"
PHOTO_CACHE = CACHE / "photos"
OCR_CACHE = CACHE / "ocr"
PICK_CACHE = CACHE / "picks"
HOLIDAY_CACHE = CACHE / "holidays.json"
HKT = timezone(timedelta(hours=8))
PUBLIC_HOLIDAYS: list[str] = []
HOLIDAY_URL = "https://www.1823.gov.hk/common/ical/tc.json"
WEEKDAY_LABELS = {
    0: "星期日",
    1: "星期一",
    2: "星期二",
    3: "星期三",
    4: "星期四",
    5: "星期五",
    6: "星期六",
}
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
DISTRICT_ID = 2008
SEARCH_ROWS = 50
MENU_ROWS = 50
PHOTO_ROWS = 100
FOOD_ROWS = 50
ALBUM_ROWS = 50
ALBUM_LIMIT = 50
API_GAP = 0.2
IMAGE_GAP = 0.1
NET_SLOTS = 8
MENU_LIMIT = 5
PHOTO_KEYS = ("menu", "environment", "food")
# OpenRice list filters: 7 餐牌 (matches menuPhotoCount), 2 環境, 1 食物 (dish captions).
PHOTO_TYPE_IDS = (("menu", 7), ("environment", 2), ("food", 1))
MENU_SCAN = 15
PICK_VERSION = 4
COVER_EDGE = 600
COVER_QUALITY = 72
DHASH_DUP = 8
PRICE_LABELS = {
    1: "$50以下",
    2: "$51-$100",
    3: "$101-$200",
    4: "$201-$400",
    5: "$401-$800",
    6: "$801以上",
}
LUNCH_WORD = re.compile(r"午市|午膳|午餐(?!肉)|\blunch\b", re.I)
TIME_24 = re.compile(
    r"(?<!\d)(1[0-2])[:.]?([0-5]\d)\s*-\s*(1[4-5])[:.]?([0-5]\d)(?!\d)"
)
TIME_CLOCK = re.compile(
    r"(?<!\d)(1[0-2])[:.]([0-5]\d)\s*(am|pm)?\s*-\s*(1?\d)[:.]([0-5]\d)\s*(am|pm)?",
    re.I,
)
TIME_CN = re.compile(
    r"(十[一二]?|[0-9]{1,2})\s*[時点點]\s*(?:([0-5]?\d)\s*分|半)?\s*-\s*"
    r"(?:下午|pm)?\s*(十[四五]?|[0-9]{1,2}|[一二兩三])\s*[時点點]\s*(?:([0-5]?\d)\s*分|半)?"
)
CN_HOUR = {
    "一": 1,
    "二": 2,
    "兩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "十": 10,
    "十一": 11,
    "十二": 12,
    "十四": 14,
    "十五": 15,
}
MEAL_ORDER = ("早市", "午市", "下午茶", "晚市")
MENU_WORD = re.compile(r"餐牌|菜單|menu|套餐|set\s*menu|價錢", re.I)
PRICE_TOKEN = re.compile(r"(?:HK\$|\$)\s?\d{2,4}")
REVIEW_WORD = re.compile(r"則評論|reviews?", re.I)
DISTRICTS = {
    "tst": {
        "id": "tst",
        "name": "尖沙咀",
        "openrice_district_id": 2008,
        "origin": {
            "id": "mira-place-1",
            "label": "美麗華廣場一期",
            "address": "尖沙咀彌敦道132號",
            "lat": 22.301111,
            "lng": 114.172222,
        },
    },
    "pe": {
        "id": "pe",
        "name": "太子",
        "openrice_district_id": 2029,
        "origin": {
            "id": "pe-station",
            "label": "太子站",
            "address": "太子彌敦道",
            "lat": 22.3245,
            "lng": 114.1683,
        },
    },
    "mk": {
        "id": "mk",
        "name": "旺角",
        "openrice_district_id": 2010,
        "origin": {
            "id": "mk-station",
            "label": "旺角站",
            "address": "旺角彌敦道",
            "lat": 22.3191,
            "lng": 114.1694,
        },
    },
    "ssp": {
        "id": "ssp",
        "name": "深水埗",
        "openrice_district_id": 2019,
        "origin": {
            "id": "ssp-station",
            "label": "深水埗站",
            "address": "深水埗長沙灣道",
            "lat": 22.3307,
            "lng": 114.1623,
        },
    },
    "jordan": {
        "id": "jordan",
        "name": "佐敦",
        "openrice_district_id": 2028,
        "origin": {
            "id": "jordan-station",
            "label": "佐敦站",
            "address": "佐敦彌敦道",
            "lat": 22.3049,
            "lng": 114.1718,
        },
    },
    "ymt": {
        "id": "ymt",
        "name": "油麻地",
        "openrice_district_id": 2011,
        "origin": {
            "id": "ymt-station",
            "label": "油麻地站",
            "address": "油麻地彌敦道",
            "lat": 22.3129,
            "lng": 114.1707,
        },
    },
    "csw": {
        "id": "csw",
        "name": "長沙灣",
        "openrice_district_id": 2013,
        "origin": {
            "id": "csw-station",
            "label": "長沙灣站",
            "address": "長沙灣長沙灣道",
            "lat": 22.3354,
            "lng": 114.1563,
        },
    },
    "lck": {
        "id": "lck",
        "name": "荔枝角",
        "openrice_district_id": 2016,
        "origin": {
            "id": "lck-station",
            "label": "荔枝角站",
            "address": "荔枝角長沙灣道",
            "lat": 22.3373,
            "lng": 114.1482,
        },
    },
    "tw": {
        "id": "tw",
        "name": "荃灣",
        "openrice_district_id": 3018,
        "origin": {
            "id": "tw-station",
            "label": "荃灣站",
            "address": "荃灣青山公路",
            "lat": 22.3736,
            "lng": 114.1178,
        },
    },
    "tm": {
        "id": "tm",
        "name": "屯門",
        "openrice_district_id": 3005,
        "origin": {
            "id": "tm-station",
            "label": "屯門站",
            "address": "屯門杯渡路",
            "lat": 22.3952,
            "lng": 113.9731,
        },
    },
}
ACTIVE = DISTRICTS["tst"]


def apply_district(slug: str) -> None:
    global ACTIVE, DATA_PATH, PHOTO_DIR, DISTRICT_ID
    district = DISTRICTS.get(slug)
    if district is None:
        known = ", ".join(sorted(DISTRICTS))
        raise SystemExit(f"unknown district {slug}; known: {known}")
    ACTIVE = district
    DATA_PATH = ROOT / "data" / slug / "restaurants.json"
    PHOTO_DIR = DATA_PATH.parent / "photos"
    DISTRICT_ID = int(district["openrice_district_id"])


def search_cache_dir() -> Path:
    if ACTIVE["id"] == "tst":
        return SEARCH_CACHE
    return SEARCH_CACHE / ACTIVE["id"]


class Limiter:
    def __init__(self, gap: float, slots: int) -> None:
        self.gap = gap
        self._sem = threading.Semaphore(slots)
        self._lock = threading.Lock()
        self._next = 0.0

    def slot(self):
        return _Slot(self)


class _Slot:
    def __init__(self, limiter: Limiter) -> None:
        self._limiter = limiter

    def __enter__(self) -> None:
        self._limiter._sem.acquire()
        with self._limiter._lock:
            wait = self._limiter._next - time.time()
            if wait > 0:
                time.sleep(wait)
            self._limiter._next = time.time() + self._limiter.gap

    def __exit__(self, exc_type, exc, tb) -> None:
        self._limiter._sem.release()


OPENRICE = Limiter(API_GAP, NET_SLOTS)
IMAGES_NET = Limiter(IMAGE_GAP, NET_SLOTS)
_publish_lock = threading.Lock()


def log(message: str) -> None:
    print(message, flush=True)


def read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def write_compact_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def http_get(url: str, limiter: Limiter, *, referer: str) -> tuple[int, bytes]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "*/*",
            "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
            "Referer": referer,
        },
    )
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            with limiter.slot():
                with urllib.request.urlopen(req, timeout=45) as resp:
                    return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            if exc.code in (403, 429, 500, 502, 503, 504) and attempt < 5:
                wait = min(60.0, 4.0 * (2**attempt))
                log(f"  backoff HTTP {exc.code} {wait:.0f}s")
                time.sleep(wait)
                continue
            return exc.code, body
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"fetch failed: {url} ({last_error})")


def fetch_json(url: str, limiter: Limiter, *, referer: str) -> dict:
    status, body = http_get(url, limiter, referer=referer)
    if status != 200:
        raise RuntimeError(f"HTTP {status} {url}")
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("JSON object expected")
    return payload


def normalize_text(text: str) -> str:
    trans = str.maketrans(
        {
            "０": "0",
            "１": "1",
            "２": "2",
            "３": "3",
            "４": "4",
            "５": "5",
            "６": "6",
            "７": "7",
            "８": "8",
            "９": "9",
            "：": ":",
            "．": ".",
            "。": ".",
            "－": "-",
            "—": "-",
            "–": "-",
            "～": "-",
            "~": "-",
            "至": "-",
            "到": "-",
        }
    )
    return text.translate(trans).replace("下午茶", " ")


def cn_hour(token: str) -> int | None:
    if token in CN_HOUR:
        return CN_HOUR[token]
    if token.isdigit():
        return int(token)
    return None


def accept_lunch_window(start_h: int, start_m: int, end_h: int, end_m: int) -> bool:
    if start_h < 10 or start_h > 12 or start_m > 59 or end_m > 59:
        return False
    if end_h == 15 and end_m > 30:
        return False
    if end_h < 14 or end_h > 15:
        return False
    span = (end_h * 60 + end_m) - (start_h * 60 + start_m)
    return 30 <= span <= 360


def resolve_end_hour(start_h: int, end_h: int, marker: str) -> int:
    flag = marker.lower()
    if flag in ("pm", "下午"):
        return end_h + 12 if end_h < 12 else end_h
    if flag in ("am", "上午"):
        return end_h
    if end_h >= 13:
        return end_h
    if end_h in (1, 2, 3) and start_h >= 10:
        return end_h + 12
    return end_h


def lunch_evidence(text: str) -> str | None:
    cleaned = normalize_text(text)
    if LUNCH_WORD.search(cleaned):
        return "午市"
    for match in TIME_24.finditer(cleaned):
        if accept_lunch_window(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            int(match.group(4)),
        ):
            return "午市"
    for match in TIME_CLOCK.finditer(cleaned):
        end_marker = (match.group(6) or "").lower()
        end_h_raw = int(match.group(4))
        if end_h_raw < 13 and end_marker != "pm":
            continue
        end_h = resolve_end_hour(int(match.group(1)), end_h_raw, end_marker)
        if accept_lunch_window(int(match.group(1)), int(match.group(2)), end_h, int(match.group(5))):
            return "午市"
    for match in TIME_CN.finditer(cleaned):
        start_h = cn_hour(match.group(1))
        end_h = cn_hour(match.group(3))
        if start_h is None or end_h is None:
            continue
        start_m = 30 if match.group(2) is None and "半" in match.group(0).split("-")[0] else int(match.group(2) or 0)
        end_token = match.group(0).split("-")[-1]
        end_m = 30 if match.group(4) is None and "半" in end_token else int(match.group(4) or 0)
        end_h = resolve_end_hour(start_h, end_h, "下午" if "下午" in match.group(0) else "")
        if accept_lunch_window(start_h, start_m, end_h, end_m):
            return "午市"
    return None


def meal_label(text: str) -> str:
    raw = text or ""
    found: list[str] = []
    if re.search(r"早市|早餐|breakfast|\bmorning\b", raw, re.I):
        found.append("早市")
    if lunch_evidence(raw):
        found.append("午市")
    if re.search(r"下午茶|afternoon\s*tea", raw, re.I):
        found.append("下午茶")
    if re.search(r"晚市|晚餐|\bdinner\b|\bsupper\b", raw, re.I):
        found.append("晚市")
    ordered = [label for label in MEAL_ORDER if label in found]
    return "／".join(ordered) if ordered else "餐牌"


def is_menu_text(text: str) -> bool:
    if not text.strip():
        return False
    if meal_label(text) != "餐牌":
        return True
    if MENU_WORD.search(text):
        return True
    return len(PRICE_TOKEN.findall(text)) >= 2


def self_check() -> None:
    cases = {
        "午市套餐 $88": "午市",
        "Lunch Set": "午市",
        "business lunch": "午市",
        "下午茶套餐": "下午茶",
        "午餐肉飯": "餐牌",
        "11:30-14:30": "午市",
        "12:00–15:00": "午市",
        "11:30-15:45": "餐牌",
        "11:00-22:00": "餐牌",
        "11:30am-2:30pm": "午市",
        "11:30-2:30": "餐牌",
        "12:00-3:00": "餐牌",
        "12:00-15:00": "午市",
        "18:00-22:00": "餐牌",
        "晚市套餐": "晚市",
        "早餐及晚餐": "早市／晚市",
        "上午11時30分至下午2時30分": "午市",
        "十二時至二時半": "午市",
    }
    failed = False
    for text, expect in cases.items():
        got = meal_label(text)
        if got != expect:
            failed = True
            log(f"FAIL {text!r} -> {got!r} expect {expect}")
    if failed:
        raise SystemExit(1)
    monday_closed = parse_hours(
        [
            {"dayOfWeek": 2, "isClose": True, "period1Start": "12:00:00", "period1End": "22:00:00"},
            {"dayOfWeek": 3, "period1Start": "12:00:00", "period1End": "22:00:00"},
            {"dayOfWeek": 1, "period1Start": "12:00:00", "period1End": "22:00:00"},
        ]
    )
    overnight = parse_hours(
        [{"dayOfWeek": 2, "period1Start": "12:00:00", "period1End": "02:00:00"}]
    )
    split = parse_hours(
        [
            {
                "dayOfWeek": 4,
                "period1Start": "12:00:00",
                "period1End": "15:00:00",
                "period2Start": "17:00:00",
                "period2End": "22:00:00",
                "isHoliday": False,
            },
            {
                "dayOfWeek": 0,
                "isHoliday": True,
                "period1Start": "12:00:00",
                "period1End": "23:00:00",
            },
        ]
    )
    midnight = parse_hours([{"dayOfWeek": 6, "period1Start": "17:30:00", "period1End": "00:00:00"}])
    if not monday_closed or monday_closed["week"]["1"] != [] or monday_closed["week"]["2"] != [["12:00", "22:00"]]:
        raise SystemExit("hours: Monday close mapping failed")
    if not overnight or overnight["week"]["1"] != [["12:00", "02:00"]]:
        raise SystemExit("hours: overnight range failed")
    if not split or split["week"]["3"] != [["12:00", "15:00"], ["17:00", "22:00"]] or split["holiday"] != [["12:00", "23:00"]]:
        raise SystemExit("hours: split day or holiday failed")
    if not midnight or midnight["week"]["5"] != [["17:30", "24:00"]]:
        raise SystemExit("hours: midnight close failed")
    if parse_hours([]) is not None or parse_hours(None) is not None:
        raise SystemExit("hours: empty should be null")
    bookable, url = booking_fields(
        {"tableMapUrl": "https://www.tablemap.com/tc/mobile/detailwithcache?app=1&poiid=1", "tmBookingWidget": {"isBookingDisabled": False}}
    )
    disabled, disabled_url = booking_fields(
        {"tableMapUrl": "https://www.tablemap.com/tc/mobile/detailwithcache?app=1&poiid=2", "tmBookingWidget": {"isBookingDisabled": True}}
    )
    if not bookable or not url or disabled or disabled_url:
        raise SystemExit("booking flag failed")
    log(f"self-check ok ({len(cases)} meal cases)")


def photo_url(photo: dict, kind: str) -> str:
    urls = photo.get("urls")
    if isinstance(urls, dict):
        value = urls.get(kind)
        if isinstance(value, str) and value.startswith("http"):
            return value
    if kind == "full":
        value = photo.get("url")
        if isinstance(value, str) and value.startswith("http"):
            return value
    return ""


def photo_source(photo: dict) -> str:
    short = photo.get("shortenUrl")
    if isinstance(short, str) and short.startswith("http"):
        return short
    photo_id = photo.get("photoId")
    if isinstance(photo_id, int):
        return f"https://www.openrice.com/zh/hongkong/photo/{photo_id}"
    full = photo_url(photo, "full")
    return full


def photo_key(photo: dict) -> str:
    photo_id = photo.get("photoId")
    if isinstance(photo_id, int):
        return str(photo_id)
    digest = hashlib.sha1(photo_url(photo, "full").encode("utf-8")).hexdigest()
    return digest[:16]


def sort_recent(photos: list[dict]) -> list[dict]:
    return sorted(
        photos,
        key=lambda photo: photo.get("submitTime") if isinstance(photo.get("submitTime"), str) else "",
        reverse=True,
    )


def as_photo_list(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def cuisine_names(categories: object) -> list[str]:
    names: list[str] = []
    if not isinstance(categories, list):
        return names
    for category in categories:
        if not isinstance(category, dict):
            continue
        search_key = category.get("searchKey")
        is_cuisine = category.get("categoryTypeId") == 1 or (
            isinstance(search_key, str) and search_key.startswith("cuisineId=")
        )
        name = category.get("name")
        if is_cuisine and isinstance(name, str) and name and name not in names:
            names.append(name)
    return names


def restaurant_url(call_name: str, poi_id: int, shorten: str) -> str:
    if call_name:
        slug = urllib.parse.quote(call_name, safe="-")
        return f"https://www.openrice.com/zh/hongkong/r-{slug}-r{poi_id}"
    return shorten


def coord(value: object, low: float, high: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if low <= number <= high:
        return number
    return None


def clock(value: object) -> str | None:
    if not isinstance(value, str) or len(value) < 5:
        return None
    parts = value.split(":")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    hour = int(parts[0])
    minute = int(parts[1])
    if hour == 24 and minute == 0:
        return "24:00"
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return f"{hour:02d}:{minute:02d}"
    return None


def row_ranges(row: dict) -> list[list[str]]:
    if row.get("isClose") is True:
        return []
    if row.get("is24hr") is True:
        return [["00:00", "24:00"]]
    ranges: list[list[str]] = []
    for index in (1, 2, 3):
        start = clock(row.get(f"period{index}Start"))
        end = clock(row.get(f"period{index}End"))
        if not start or not end:
            continue
        if start == "00:00" and end == "00:00":
            return [["00:00", "24:00"]]
        if end == "00:00":
            end = "24:00"
        ranges.append([start, end])
    return ranges


def iso_date(value: object) -> str | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    head = value[:10]
    if len(head) == 10 and head[4] == "-" and head[7] == "-" and head[:4].isdigit():
        return head
    return None


def js_weekday(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if 1 <= value <= 7:
        return value - 1
    return None


def format_ranges(ranges: list[list[str]]) -> str:
    if not ranges:
        return "休息"
    if ranges == [["00:00", "24:00"]]:
        return "24小時"
    return "、".join(f"{start}–{end}" for start, end in ranges)


def hours_text(
    week: dict[str, list[list[str]]],
    uncertain: set[str],
    holiday: list[list[str]] | None,
    holiday_eve: list[list[str]] | None,
    monthly: list[dict],
    specials: list[dict],
) -> str:
    lines: list[str] = []
    if week:
        order = [1, 2, 3, 4, 5, 6, 0]
        bodies = [week.get(str(day), []) for day in order]
        if len(week) == 7 and all(body == bodies[0] for body in bodies) and not uncertain:
            lines.append("星期一至日 " + format_ranges(bodies[0]))
        else:
            for day in order:
                key = str(day)
                if key not in week:
                    continue
                note = "（時間未確定）" if key in uncertain else ""
                lines.append(f"{WEEKDAY_LABELS[day]} {format_ranges(week[key])}{note}")
    if holiday is not None:
        lines.append("公眾假期 " + format_ranges(holiday))
    if holiday_eve is not None:
        lines.append("公眾假期前夕 " + format_ranges(holiday_eve))
    for item in monthly:
        day = item.get("day")
        week_index = item.get("week")
        ranges = item.get("ranges")
        if not isinstance(day, int) or day not in WEEKDAY_LABELS:
            continue
        if not isinstance(week_index, int) or not isinstance(ranges, list):
            continue
        lines.append(f"每月第{week_index}個{WEEKDAY_LABELS[day]} {format_ranges(ranges)}")
    for item in specials:
        start = item.get("from")
        end = item.get("to")
        ranges = item.get("ranges")
        note = item.get("note") if isinstance(item.get("note"), str) else ""
        if not isinstance(start, str) or not isinstance(end, str) or not isinstance(ranges, list):
            continue
        span = start if start == end else f"{start}至{end}"
        label = f" {note}" if note else ""
        lines.append(f"特別 {span}{label} {format_ranges(ranges)}")
    return "\n".join(lines)


def parse_hours(value: object) -> dict | None:
    """Turn OpenRice poiHours into weekday ranges plus the raw summary.

    dayOfWeek 1 is Sunday. A period ending at 00:00 closes at midnight;
    an end earlier than the start runs past midnight.
    """
    if not isinstance(value, list):
        return None
    rows = [item for item in value if isinstance(item, dict)]
    if not rows:
        return None
    week: dict[str, list[list[str]]] = {}
    uncertain: set[str] = set()
    monthly: list[dict] = []
    specials: list[dict] = []
    holiday: list[list[str]] | None = None
    holiday_eve: list[list[str]] | None = None
    saw_weekday = False
    for row in rows:
        start = iso_date(row.get("dateFrom"))
        end = iso_date(row.get("dateTo"))
        if start and end:
            note = row.get("displayNameLang1")
            specials.append(
                {
                    "from": start,
                    "to": end,
                    "ranges": row_ranges(row),
                    "note": note if isinstance(note, str) else "",
                }
            )
            continue
        if row.get("isHoliday") is True:
            holiday = row_ranges(row)
            continue
        if row.get("isHolidayEve") is True:
            holiday_eve = row_ranges(row)
            continue
        weekday = js_weekday(row.get("dayOfWeek"))
        if weekday is None:
            continue
        week_of_month = row.get("weekOfMonth")
        if isinstance(week_of_month, int) and not isinstance(week_of_month, bool) and week_of_month > 0:
            monthly.append({"day": weekday, "week": week_of_month, "ranges": row_ranges(row)})
            continue
        saw_weekday = True
        key = str(weekday)
        week[key] = row_ranges(row)
        if row.get("isUncertain") is True:
            uncertain.add(key)
    if saw_weekday:
        for day in range(7):
            week.setdefault(str(day), [])
    if not saw_weekday and holiday is None and holiday_eve is None and not specials and not monthly:
        return None
    specials.sort(key=lambda item: (item["from"], item["to"]))
    return {
        "week": week,
        "holiday": holiday,
        "holiday_eve": holiday_eve,
        "specials": specials,
        "monthly": monthly,
        "text": hours_text(week, uncertain, holiday, holiday_eve, monthly, specials),
    }


def booking_fields(row: dict) -> tuple[bool, str | None]:
    """Online 訂座 when OpenRice exposes a TableMap page and booking is enabled."""
    url = row.get("tableMapUrl")
    booking_url = url if isinstance(url, str) and url.startswith("http") else ""
    poi = row.get("tableMapPoiId")
    if not booking_url and isinstance(poi, int) and not isinstance(poi, bool) and poi > 0:
        booking_url = f"https://www.tablemap.com/tc/mobile/detailwithcache?app=1&poiid={poi}"
    widget = row.get("tmBookingWidget")
    disabled = isinstance(widget, dict) and widget.get("isBookingDisabled") is True
    if not booking_url or disabled:
        return False, None
    return True, booking_url


def holiday_dates(payload: dict) -> list[str]:
    found: list[str] = []
    calendars = payload.get("vcalendar")
    if not isinstance(calendars, list):
        return []
    for calendar in calendars:
        if not isinstance(calendar, dict):
            continue
        events = calendar.get("vevent")
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            start_raw = event.get("dtstart")
            end_raw = event.get("dtend")
            start_token = start_raw[0] if isinstance(start_raw, list) and start_raw else start_raw
            end_token = end_raw[0] if isinstance(end_raw, list) and end_raw else end_raw
            if not isinstance(start_token, str) or len(start_token) < 8 or not start_token[:8].isdigit():
                continue
            start = date(int(start_token[0:4]), int(start_token[4:6]), int(start_token[6:8]))
            if isinstance(end_token, str) and len(end_token) >= 8 and end_token[:8].isdigit():
                end = date(int(end_token[0:4]), int(end_token[4:6]), int(end_token[6:8]))
            else:
                end = start + timedelta(days=1)
            cursor = start
            while cursor < end:
                found.append(cursor.isoformat())
                cursor += timedelta(days=1)
    return sorted(set(found))


def fetch_holidays(*, use_cache: bool) -> list[str]:
    if use_cache:
        cached = read_json(HOLIDAY_CACHE)
        if cached and isinstance(cached.get("dates"), list) and cached["dates"]:
            return [item for item in cached["dates"] if isinstance(item, str)]
    status, body = http_get(HOLIDAY_URL, OPENRICE, referer="https://www.1823.gov.hk/")
    if status != 200:
        raise RuntimeError(f"HTTP {status} holidays")
    payload = json.loads(body.decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError("holiday calendar was not an object")
    dates = holiday_dates(payload)
    if not dates:
        raise RuntimeError("holiday calendar had no dates")
    write_json(HOLIDAY_CACHE, {"dates": dates})
    return dates


def load_holidays(*, use_cache: bool) -> None:
    global PUBLIC_HOLIDAYS
    try:
        PUBLIC_HOLIDAYS = fetch_holidays(use_cache=use_cache)
        log(f"public holidays {len(PUBLIC_HOLIDAYS)}")
    except (RuntimeError, json.JSONDecodeError, UnicodeError, ValueError) as exc:
        PUBLIC_HOLIDAYS = []
        log(f"holiday calendar unavailable: {exc}")


def search_page(start_at: int, *, use_cache: bool, price_range_id: int | None = None) -> dict:
    slug = f"price-{price_range_id}" if price_range_id else "district"
    path = search_cache_dir() / f"{slug}-r{SEARCH_ROWS}-{start_at}.json"
    if use_cache:
        cached = read_json(path)
        if cached:
            return cached
    params = {
        "uiLang": "zh",
        "uiCity": "hongkong",
        "districtId": DISTRICT_ID,
        "startAt": start_at,
        "rows": SEARCH_ROWS,
        "sortBy": "ORScoreDesc",
    }
    if price_range_id:
        params["priceRangeId"] = price_range_id
    url = "https://www.openrice.com/api/v2/search?" + urllib.parse.urlencode(params)
    payload = fetch_json(
        url,
        OPENRICE,
        referer=f"https://www.openrice.com/zh/hongkong/restaurants?districtId={DISTRICT_ID}",
    )
    write_json(path, payload)
    return payload


def search_results(page: dict) -> list[dict]:
    pagination = page.get("paginationResult")
    if isinstance(pagination, dict):
        results = pagination.get("results")
        if isinstance(results, list):
            return [item for item in results if isinstance(item, dict)]
    return []


def page_count(page: dict) -> int | None:
    pagination = page.get("paginationResult")
    if not isinstance(pagination, dict):
        return None
    count = pagination.get("count")
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        return count
    return None


def paginate(
    seen: set[int], *, use_cache: bool, price_range_id: int | None, max_scan: int
) -> tuple[list[dict], int | None]:
    chosen: list[dict] = []
    start = 0
    stale_pages = 0
    reported: int | None = None
    while start < max_scan:
        page = search_page(start, use_cache=use_cache, price_range_id=price_range_id)
        if reported is None:
            reported = page_count(page)
        results = search_results(page)
        if not results:
            break
        fresh = 0
        for row in results:
            poi_id = row.get("poiId")
            name = row.get("name")
            if not isinstance(poi_id, int) or isinstance(poi_id, bool) or poi_id in seen:
                continue
            if not isinstance(name, str) or not name.strip():
                continue
            seen.add(poi_id)
            chosen.append(row)
            fresh += 1
        label = f"price {price_range_id}" if price_range_id else "district"
        log(f"search {label} start={start} page={len(results)} new={fresh} unique={len(seen)}")
        if fresh == 0:
            stale_pages += 1
            if stale_pages >= 2:
                break
        else:
            stale_pages = 0
        if len(results) < SEARCH_ROWS:
            break
        start += SEARCH_ROWS
    return chosen, reported


def collect_rows(target: int, max_scan: int, *, use_cache: bool) -> list[dict]:
    seen: set[int] = set()
    chosen, reported = paginate(seen, use_cache=use_cache, price_range_id=None, max_scan=max_scan)
    if reported is not None and len(chosen) < reported:
        log(f"district list {len(chosen)} short of {reported}; splitting by price")
        for price_range_id in range(1, 7):
            extra, _reported = paginate(
                seen, use_cache=use_cache, price_range_id=price_range_id, max_scan=max_scan
            )
            chosen.extend(extra)
            if len(chosen) >= reported:
                break
    if target > 0:
        chosen = chosen[:target]
    log(f"search kept {len(chosen)} restaurants reported={reported}")
    return chosen


def empty_photo_counts() -> dict[str, int]:
    return {key: 0 for key in PHOTO_KEYS}


def submit_time(photo: dict) -> str:
    value = photo.get("submitTime")
    if isinstance(value, str) and len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return value
    return ""


def photo_caption(photo: dict) -> str:
    for key in ("caption", "otherCaption"):
        value = photo.get(key)
        if not isinstance(value, str):
            continue
        text = re.sub(r"\s+", " ", value).strip()
        if text:
            return text[:80]
    return ""


def derived_thumb(full: str) -> str:
    match = re.match(r"^(.*)(?:px|lx|lv|tx)\.(jpe?g|webp|png)$", full, re.I)
    if not match:
        return ""
    return f"{match.group(1)}sx.{match.group(2)}"


def compact_photo(photo: dict) -> dict | None:
    full = photo_url(photo, "full") or photo_url(photo, "standard")
    if not full:
        return None
    thumb = photo_url(photo, "thumbnail") or photo_url(photo, "icon") or full
    item: dict[str, str] = {"full": full}
    if thumb and thumb != derived_thumb(full):
        item["thumb"] = thumb
    when = submit_time(photo)
    if when:
        item["time"] = when
    caption = photo_caption(photo)
    if caption:
        item["caption"] = caption
    source = photo_source(photo)
    if source.startswith("http") and "openrice.com" in source:
        item["source"] = source
    return item


def photo_page(poi_id: int, type_id: int, start: int, *, use_cache: bool, rows: int) -> dict:
    path = PHOTO_CACHE / f"{poi_id}-{type_id}-{start}.json"
    if use_cache:
        cached = read_json(path)
        if cached and isinstance(cached.get("results"), list):
            return cached
    url = (
        "https://www.openrice.com/api/v2/media/photo"
        f"?uiLang=zh&uiCity=hongkong&poiId={poi_id}&photoTypeId={type_id}"
        f"&startAt={start}&rows={rows}"
    )
    payload = fetch_json(url, OPENRICE, referer="https://www.openrice.com/")
    write_json(path, payload)
    return payload


def reported_count(payload: dict) -> int | None:
    count = payload.get("count")
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        return count
    return None


def collect_album_photos(
    poi_id: int,
    type_id: int,
    *,
    use_cache: bool,
    limit: int | None = None,
    rows: int = PHOTO_ROWS,
) -> tuple[list[dict], int]:
    seen: set[str] = set()
    items: list[dict] = []
    start = 0
    reported: int | None = None
    while start < 20000:
        payload = photo_page(poi_id, type_id, start, use_cache=use_cache, rows=rows)
        if reported is None:
            reported = reported_count(payload)
        results = as_photo_list(payload.get("results"))
        if not results:
            break
        fresh = 0
        for photo in results:
            key = photo_key(photo)
            if key in seen:
                continue
            seen.add(key)
            item = compact_photo(photo)
            if item is None:
                continue
            fresh += 1
            items.append(item)
            if limit is not None and len(items) >= limit:
                break
        if limit is not None or fresh == 0 or len(results) < rows:
            break
        start += rows
    if limit is not None:
        items = items[:limit]
    items.sort(key=lambda item: item.get("time") or "", reverse=True)
    total = reported if reported is not None else len(items)
    return items, max(total, len(items))


def borrowed_album(poi_id: int) -> tuple[Path, dict[str, int]] | None:
    """Reuse a photo file already stored for this poi in another district."""
    for slug in DISTRICTS:
        if slug == ACTIVE["id"]:
            continue
        path = ROOT / "data" / slug / "photos" / f"{poi_id}.json"
        payload = read_json(path)
        if not payload:
            continue
        counts: dict[str, int] = {}
        complete = True
        for key in PHOTO_KEYS:
            total = payload.get(f"{key}_total")
            if not isinstance(total, int) or isinstance(total, bool) or total < 0:
                complete = False
                break
            counts[key] = total
        if complete:
            return path, counts
    return None


def save_album(poi_id: int, *, use_cache: bool) -> dict[str, int]:
    done_path = PHOTO_CACHE / f"{poi_id}.done.json"
    out_path = PHOTO_DIR / f"{poi_id}.json"
    if use_cache and done_path.is_file():
        done = read_json(done_path)
        if done and all(isinstance(done.get(key), int) and not isinstance(done.get(key), bool) for key in PHOTO_KEYS):
            counts = {key: int(done[key]) for key in PHOTO_KEYS}
            if not any(counts.values()) or out_path.is_file():
                return counts
    borrowed = borrowed_album(poi_id)
    if borrowed is not None:
        source, counts = borrowed
        if any(counts.values()):
            out_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, out_path)
        elif out_path.exists():
            out_path.unlink()
        write_json(done_path, counts)
        return counts
    album: dict[str, list[dict] | int] = {}
    counts: dict[str, int] = {}
    for key, type_id in PHOTO_TYPE_IDS:
        items, total = collect_album_photos(
            poi_id, type_id, use_cache=use_cache, limit=ALBUM_LIMIT, rows=ALBUM_ROWS
        )
        album[key] = items
        album[f"{key}_total"] = total
        counts[key] = total
    if any(counts.values()):
        write_compact_json(out_path, album)
    elif out_path.exists():
        out_path.unlink()
    write_json(done_path, counts)
    return counts


def apply_photo_counts(record: dict, counts: dict[str, int]) -> None:
    record["photo_counts"] = {key: counts.get(key, 0) for key in PHOTO_KEYS}
    record.pop("menus", None)


def photo_count(row: dict, key: str) -> int:
    counts = row.get("photo_counts")
    if isinstance(counts, dict):
        value = counts.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    if key == "menu":
        menus = row.get("menus")
        if isinstance(menus, list):
            return len(menus)
    return 0


def menu_payload(poi_id: int, *, use_cache: bool) -> dict:
    path = MENU_CACHE / f"{poi_id}.json"
    if use_cache:
        cached = read_json(path)
        if cached:
            return cached
    url = (
        f"https://www.openrice.com/api/v2/media/photo/{poi_id}/menu"
        f"?uiLang=zh&uiCity=hongkong&startAt=0&rows={MENU_ROWS}"
    )
    payload = fetch_json(url, OPENRICE, referer="https://www.openrice.com/")
    write_json(path, payload)
    return payload


def load_image(url: str):
    from PIL import Image

    status, body = http_get(url, IMAGES_NET, referer="https://www.openrice.com/")
    if status != 200 or len(body) < 800:
        raise RuntimeError(f"image HTTP {status} bytes {len(body)}")
    return Image.open(io.BytesIO(body))


class OcrEngine:
    def __init__(self) -> None:
        self.kind = "none"
        self._rapid = None
        self._tesseract = shutil.which("tesseract")
        try:
            from rapidocr_onnxruntime import RapidOCR

            self._rapid = RapidOCR()
            self.kind = "rapidocr"
            return
        except Exception as exc:
            log(f"RapidOCR unavailable: {exc}")
        if self._tesseract:
            self.kind = "tesseract"
            return
        log("No OCR engine. Caption matches only.")

    def read(self, image) -> str:
        if self.kind == "rapidocr" and self._rapid is not None:
            work = image.copy()
            work.thumbnail((640, 640))
            if work.mode != "RGB":
                work = work.convert("RGB")
            result, _elapsed = self._rapid(work, use_cls=False)
            lines: list[str] = []
            if isinstance(result, list):
                for item in result:
                    if isinstance(item, (list, tuple)) and len(item) >= 2 and isinstance(item[1], str):
                        lines.append(item[1])
            return "\n".join(lines)
        if self.kind == "tesseract" and self._tesseract:
            work = image.copy()
            work.thumbnail((1600, 1600))
            if work.mode not in ("RGB", "L"):
                work = work.convert("RGB")
            buf = io.BytesIO()
            work.save(buf, format="PNG")
            proc = subprocess.run(
                [self._tesseract, "stdin", "stdout", "-l", "chi_tra+eng", "--psm", "11"],
                input=buf.getvalue(),
                capture_output=True,
                timeout=45,
                check=False,
            )
            return proc.stdout.decode("utf-8", "replace")
        return ""


def ocr_text(engine: OcrEngine, photo: dict, image, *, use_cache: bool) -> str:
    key = photo_key(photo)
    path = OCR_CACHE / f"{key}.txt"
    if use_cache and path.exists():
        return path.read_text(encoding="utf-8")
    try:
        text = engine.read(image)
    except Exception as exc:
        log(f"  ocr skip {key}: {exc}")
        return ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def dhash_int(image) -> int:
    from PIL import Image

    small = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(small.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            left = pixels[row * 9 + col]
            right = pixels[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def write_jpeg(image, relative: str, *, max_edge: int, quality: int) -> None:
    img = image.convert("RGB") if image.mode != "RGB" else image.copy()
    img.thumbnail((max_edge, max_edge))
    dest = ROOT / relative
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=quality, optimize=True)


def cover_is_small(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 800:
        return False
    try:
        from PIL import Image

        with Image.open(path) as image:
            return max(image.size) <= COVER_EDGE + 40
    except OSError:
        return False


def file_ready(relative: str) -> bool:
    path = ROOT / relative
    return path.is_file() and path.stat().st_size > 1000


def cover_url(row: dict) -> str:
    door = row.get("doorPhoto")
    if not isinstance(door, dict):
        return ""
    return photo_url(door, "standard") or photo_url(door, "full") or photo_url(door, "thumbnail")


def stored_photo_url(photo: object) -> str:
    if not isinstance(photo, dict):
        return ""
    full = photo.get("full")
    if isinstance(full, str) and full.startswith("http"):
        return full
    thumb = photo.get("thumb")
    if isinstance(thumb, str) and thumb.startswith("http"):
        return thumb
    return ""


def fallback_cover(poi_id: int, photo_dir: Path | None = None) -> str:
    """First 環境 photo, else 餐牌, else 食物. Empty when none are stored."""
    folder = photo_dir if photo_dir is not None else PHOTO_DIR
    payload = read_json(folder / f"{poi_id}.json")
    if not payload:
        return ""
    for key in ("environment", "menu", "food"):
        photos = payload.get(key)
        if not isinstance(photos, list):
            continue
        for photo in photos:
            url = stored_photo_url(photo)
            if url:
                return url
    return ""


def resolve_cover(row: dict, poi_id: int) -> str:
    door = cover_url(row)
    return door or fallback_cover(poi_id)


def save_cover(row: dict, poi_id: int, *, use_cache: bool) -> str:
    return cover_url(row)


def caption_label(photo: dict) -> str:
    caption = photo.get("caption")
    if isinstance(caption, str):
        text = re.sub(r"\s+", " ", caption).strip()
        if text:
            return text[:80]
    return "餐牌"


def menu_photos(payload: dict) -> list[dict]:
    seen: set[str] = set()
    photos: list[dict] = []
    merged = as_photo_list(payload.get("rmsMenu")) + as_photo_list(payload.get("userUploadMenu"))
    for photo in sort_recent(merged):
        if not (photo_url(photo, "full") or photo_url(photo, "standard")):
            continue
        key = photo_key(photo)
        if key in seen:
            continue
        seen.add(key)
        photos.append(photo)
    return photos


def public_menus(menus: list) -> list[dict]:
    cleaned: list[dict] = []
    for item in menus:
        if not isinstance(item, dict) or not isinstance(item.get("image"), str):
            continue
        meal = item.get("meal") if isinstance(item.get("meal"), str) and item.get("meal") else "餐牌"
        source = item.get("source_url") if isinstance(item.get("source_url"), str) else ""
        cleaned.append({"image": item["image"], "meal": meal, "source_url": source})
    return cleaned


def remote_menus(menus: list[dict]) -> bool:
    return bool(menus) and all(item["image"].startswith("http") for item in menus)


def build_menus(poi_id: int, payload: dict, *, use_cache: bool) -> list[dict]:
    pick_path = PICK_CACHE / f"{poi_id}.json"
    if use_cache:
        cached = read_json(pick_path)
        raw_menus = cached.get("menus") if cached and cached.get("version") == PICK_VERSION else None
        cleaned = public_menus(raw_menus) if isinstance(raw_menus, list) else []
        if remote_menus(cleaned):
            return cleaned

    menus: list[dict] = []
    for photo in menu_photos(payload)[:MENU_LIMIT]:
        url = photo_url(photo, "full") or photo_url(photo, "standard")
        if not url:
            continue
        menus.append(
            {
                "image": url,
                "meal": caption_label(photo),
                "source_url": photo_source(photo),
            }
        )
    if menus:
        write_json(pick_path, {"version": PICK_VERSION, "poi_id": poi_id, "menus": menus})
    return menus



def base_record(row: dict) -> dict:
    poi_id = row["poiId"]
    name = str(row.get("name") or "")
    cuisines = cuisine_names(row.get("categories"))
    price_id = row.get("priceRangeId")
    price_num = price_id if isinstance(price_id, int) else 0
    address = row.get("address") if isinstance(row.get("address"), str) else ""
    prefix = str(ACTIVE["name"])
    if address and prefix not in address:
        address = f"{prefix}{address}"
    call_name = row.get("latestCallName") if isinstance(row.get("latestCallName"), str) else ""
    shorten = row.get("shortenUrl") if isinstance(row.get("shortenUrl"), str) else ""
    smile = row.get("scoreSmile")
    cry = row.get("scoreCry")
    overall = row.get("scoreOverall")
    bookable, booking_url = booking_fields(row)
    return {
        "poi_id": poi_id,
        "name": name,
        "cuisine": cuisines[0] if cuisines else "未分類",
        "cuisines": cuisines,
        "price_range_id": price_num,
        "price_range": PRICE_LABELS.get(price_num, "價錢未列明"),
        "address": address,
        "lat": coord(row.get("mapLatitude"), 22.28, 22.48),
        "lng": coord(row.get("mapLongitude"), 113.9, 114.25),
        "score_smile": smile if isinstance(smile, int) else 0,
        "score_cry": cry if isinstance(cry, int) else 0,
        "score_overall": overall if isinstance(overall, (int, float)) and not isinstance(overall, bool) else None,
        "url": restaurant_url(call_name, poi_id, shorten),
        "district": ACTIVE["id"],
        "cover": cover_url(row),
        "photo_counts": empty_photo_counts(),
        "hours": parse_hours(row.get("poiHours")),
        "bookable": bookable,
        "booking_url": booking_url,
        "opened_on": iso_date(row.get("openSince")),
    }


def sync_district_catalog(card_count: int) -> None:
    path = ROOT / "data" / "districts.json"
    payload = read_json(path) or {"districts": []}
    districts = payload.get("districts") if isinstance(payload.get("districts"), list) else []
    origin = ACTIVE["origin"]
    entry = {
        "id": ACTIVE["id"],
        "name": ACTIVE["name"],
        "openrice_district_id": ACTIVE["openrice_district_id"],
        "origin": origin,
        "card_count": card_count,
    }
    replaced = False
    for index, item in enumerate(districts):
        if isinstance(item, dict) and item.get("id") == entry["id"]:
            districts[index] = entry
            replaced = True
            break
    if not replaced:
        districts.append(entry)
    write_json(path, {"districts": districts})


def fill_missing_covers() -> None:
    """Write a card image for restaurants that have no door photo."""
    data_root = ROOT / "data"
    if not data_root.is_dir():
        return
    for district_dir in sorted(path for path in data_root.iterdir() if path.is_dir()):
        path = district_dir / "restaurants.json"
        payload = read_json(path)
        if not payload or not isinstance(payload.get("restaurants"), list):
            continue
        records = [row for row in payload["restaurants"] if isinstance(row, dict)]
        photo_dir = district_dir / "photos"
        filled = 0
        for record in records:
            cover = record.get("cover")
            if isinstance(cover, str) and cover.startswith("http"):
                continue
            poi_id = record.get("poi_id")
            if not isinstance(poi_id, int) or isinstance(poi_id, bool):
                continue
            url = fallback_cover(poi_id, photo_dir)
            if not url:
                continue
            record["cover"] = url
            filled += 1
        if filled:
            write_json(path, payload)
        log(f"covers {district_dir.name} filled={filled}")


def publish(records: list[dict]) -> None:
    menu_images = sum(photo_count(row, "menu") for row in records)
    environment_images = sum(photo_count(row, "environment") for row in records)
    food_images = sum(photo_count(row, "food") for row in records)
    with_hours = sum(1 for row in records if isinstance(row.get("hours"), dict))
    bookable_count = sum(1 for row in records if row.get("bookable") is True)
    opened_count = sum(1 for row in records if isinstance(row.get("opened_on"), str) and row.get("opened_on"))
    district_id = int(ACTIVE["openrice_district_id"])
    payload = {
        "meta": {
            "updated_at": datetime.now(HKT).replace(microsecond=0).isoformat(),
            "search_key": ACTIVE["name"],
            "district": ACTIVE["id"],
            "district_id": district_id,
            "card_count": len(records),
            "menu_count": sum(1 for row in records if photo_count(row, "menu") > 0),
            "menu_image_count": menu_images,
            "environment_count": sum(1 for row in records if photo_count(row, "environment") > 0),
            "environment_image_count": environment_images,
            "food_count": sum(1 for row in records if photo_count(row, "food") > 0),
            "food_image_count": food_images,
            "hours_count": with_hours,
            "bookable_count": bookable_count,
            "opened_count": opened_count,
            "public_holidays": PUBLIC_HOLIDAYS,
            "source": "OpenRice",
            "source_url": f"https://www.openrice.com/zh/hongkong/restaurants?districtId={district_id}",
        },
        "restaurants": records,
    }
    with _publish_lock:
        write_json(DATA_PATH, payload)
        sync_district_catalog(len(records))


def process_row(row: dict, *, use_cache: bool) -> dict:
    record = base_record(row)
    poi_id = record["poi_id"]
    try:
        record["cover"] = save_cover(row, poi_id, use_cache=use_cache)
    except Exception as exc:
        log(f"  cover error {poi_id}: {exc}")
    try:
        apply_photo_counts(record, save_album(poi_id, use_cache=use_cache))
    except Exception as exc:
        log(f"  photo error {poi_id}: {exc}")
    if not str(record.get("cover") or "").startswith("http"):
        record["cover"] = fallback_cover(poi_id)
    return record


def drop_old_flat_images() -> int:
    removed = 0
    if not any(COVERS.glob("*.jpg")):
        return 0
    for path in IMAGES.glob("*.jpg"):
        path.unlink()
        removed += 1
    return removed


def site_megabytes() -> float:
    total = 0
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if ".git" in path.parts or "_cache" in path.parts:
            continue
        total += path.stat().st_size
    return total / 1_000_000


def drop_local_menus() -> int:
    if not MENUS.exists():
        return 0
    removed = 0
    for path in MENUS.glob("*.jpg"):
        path.unlink()
        removed += 1
    return removed


def refresh(target: int, max_scan: int, workers: int, *, use_cache: bool) -> None:
    load_holidays(use_cache=use_cache)
    rows = collect_rows(target, max_scan, use_cache=use_cache)
    if not rows:
        raise SystemExit("no restaurants")
    shells = [base_record(row) for row in rows]
    publish(shells)
    ordered = {record["poi_id"]: record for record in shells}
    order = [record["poi_id"] for record in shells]
    state_lock = threading.Lock()
    since_publish = 0
    log(f"openrice workers={workers} district={ACTIVE['id']} photos=hotlink")

    def remember(poi_id: int, fields: dict) -> None:
        nonlocal since_publish
        snapshot: list[dict] | None = None
        with state_lock:
            if "photo_counts" in fields:
                ordered[poi_id].pop("menus", None)
            ordered[poi_id].update(fields)
            since_publish += 1
            if since_publish >= 40:
                since_publish = 0
                snapshot = [ordered[item] for item in order]
        if snapshot is not None:
            publish(snapshot)

    def enrich_openrice(row: dict) -> tuple[str, int, str, dict]:
        poi_id = row["poiId"]
        cover = ""
        counts = empty_photo_counts()
        try:
            cover = save_cover(row, poi_id, use_cache=use_cache)
        except Exception as exc:
            log(f"  cover error {poi_id}: {exc}")
        try:
            counts = save_album(poi_id, use_cache=use_cache)
        except Exception as exc:
            log(f"  photo error {poi_id}: {exc}")
        if not cover.startswith("http"):
            cover = fallback_cover(poi_id)
        return ("place", poi_id, cover, counts)

    done_place = 0
    with ThreadPoolExecutor(max_workers=workers) as openrice_pool:
        futures = [openrice_pool.submit(enrich_openrice, row) for row in rows]
        for future in as_completed(futures):
            _kind, poi_id, cover, counts = future.result()
            remember(poi_id, {"cover": cover, "photo_counts": counts})
            done_place += 1
            if done_place % 50 == 0 or done_place == len(rows):
                log(f"openrice {done_place}/{len(rows)}")
    removed_flat = drop_old_flat_images()
    removed_menus = drop_local_menus()
    final = [ordered[poi_id] for poi_id in order]
    publish(final)
    menu_images = sum(photo_count(row, "menu") for row in final)
    environment_images = sum(photo_count(row, "environment") for row in final)
    food_images = sum(photo_count(row, "food") for row in final)
    with_hours = sum(1 for row in final if isinstance(row.get("hours"), dict))
    bookable_count = sum(1 for row in final if row.get("bookable") is True)
    log(
        f"DONE restaurants={len(final)} menus={menu_images} environment={environment_images} "
        f"food={food_images} hours={with_hours} bookable={bookable_count} "
        f"removed_flat={removed_flat} removed_menus={removed_menus} "
        f"site_mb={site_megabytes():.1f}"
    )


def cached_search_rows() -> dict[int, dict]:
    found: dict[int, dict] = {}
    for path in sorted(search_cache_dir().glob(f"district-r{SEARCH_ROWS}-*.json")):
        page = read_json(path)
        if not page:
            continue
        for row in search_results(page):
            poi_id = row.get("poiId")
            if isinstance(poi_id, int) and not isinstance(poi_id, bool):
                found[poi_id] = row
    return found


def patch_hours(*, use_cache: bool) -> None:
    """Attach hours and booking from the search cache. Does not refetch covers or ratings."""
    payload = read_json(DATA_PATH)
    if not payload or not isinstance(payload.get("restaurants"), list):
        raise SystemExit("missing restaurants.json")
    records = [row for row in payload["restaurants"] if isinstance(row, dict)]
    load_holidays(use_cache=use_cache)
    rows = cached_search_rows()
    matched = 0
    for record in records:
        poi_id = record.get("poi_id")
        row = rows.get(poi_id) if isinstance(poi_id, int) else None
        if row is None:
            record["hours"] = None
            record["bookable"] = False
            record["booking_url"] = None
            record["opened_on"] = None
            continue
        matched += 1
        record["hours"] = parse_hours(row.get("poiHours"))
        bookable, booking_url = booking_fields(row)
        record["bookable"] = bookable
        record["booking_url"] = booking_url
        record["opened_on"] = iso_date(row.get("openSince"))
    publish(records)
    with_hours = sum(1 for row in records if isinstance(row.get("hours"), dict))
    bookable_count = sum(1 for row in records if row.get("bookable") is True)
    opened_count = sum(1 for row in records if isinstance(row.get("opened_on"), str) and row.get("opened_on"))
    log(
        f"DONE patch restaurants={len(records)} matched={matched} hours={with_hours} "
        f"bookable={bookable_count} opened={opened_count} holidays={len(PUBLIC_HOLIDAYS)}"
    )


def merge_photo_counts(updates: dict[int, dict[str, int]]) -> None:
    """Merge one shard's counts into restaurants.json without clobbering other shards."""
    if not updates:
        return
    PHOTO_CACHE.mkdir(parents=True, exist_ok=True)
    lock = PHOTO_CACHE / "publish.lock"
    while True:
        if lock.exists():
            try:
                stale = time.time() - lock.stat().st_mtime > 45
            except OSError:
                stale = True
            if stale:
                try:
                    lock.unlink()
                except OSError:
                    pass
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            time.sleep(0.2)
    try:
        payload = read_json(DATA_PATH)
        if not payload or not isinstance(payload.get("restaurants"), list):
            raise RuntimeError("missing restaurants.json")
        records = [row for row in payload["restaurants"] if isinstance(row, dict)]
        by_id = {row.get("poi_id"): row for row in records}
        for poi_id, counts in updates.items():
            row = by_id.get(poi_id)
            if row is not None:
                apply_photo_counts(row, counts)
        publish(records)
    finally:
        os.close(fd)
        try:
            lock.unlink()
        except OSError:
            pass


def patch_photos(workers: int, *, use_cache: bool, shard: int = 0, shards: int = 1) -> None:
    """Fetch 餐牌, 環境, and the newest 50 食物 photos. Does not refetch covers or ratings."""
    payload = read_json(DATA_PATH)
    if not payload or not isinstance(payload.get("restaurants"), list):
        raise SystemExit("missing restaurants.json")
    records = [row for row in payload["restaurants"] if isinstance(row, dict)]
    if shards < 1 or shard < 0 or shard >= shards:
        raise SystemExit(f"bad shard {shard}/{shards}")
    mine = [row for index, row in enumerate(records) if shards == 1 or index % shards == shard]
    log(f"photos shard={shard}/{shards} restaurants={len(mine)} workers={workers}")
    done = 0
    failed = 0
    pending: dict[int, dict[str, int]] = {}

    def one(row: dict) -> tuple[int, dict[str, int] | None]:
        poi_id = row.get("poi_id")
        if not isinstance(poi_id, int) or isinstance(poi_id, bool):
            return -1, None
        try:
            return poi_id, save_album(poi_id, use_cache=use_cache)
        except Exception as exc:
            log(f"  photo error {poi_id}: {exc}")
            return poi_id, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, row) for row in mine]
        for future in as_completed(futures):
            poi_id, counts = future.result()
            done += 1
            if counts is not None and poi_id >= 0:
                pending[poi_id] = counts
            elif poi_id >= 0:
                failed += 1
            if pending and (done % 25 == 0 or done == len(mine)):
                merge_photo_counts(pending)
                pending = {}
                log(f"photos shard={shard} {done}/{len(mine)} failed={failed}")
    if pending:
        merge_photo_counts(pending)
    log(f"DONE photos shard={shard}/{shards} restaurants={len(mine)} failed={failed} site_mb={site_megabytes():.1f}")



def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh restaurant pages for one or more districts")
    parser.add_argument("--target", type=int, default=0, help="Stop after this many restaurants; 0 means all")
    parser.add_argument("--max-scan", type=int, default=4000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--resume", action="store_true", help="Reuse caches (also the default)")
    parser.add_argument("--force", action="store_true", help="Ignore HTTP and image caches")
    parser.add_argument("--check", action="store_true", help="Run matcher self-check and exit")
    parser.add_argument(
        "--hours-only",
        action="store_true",
        help="Attach opening hours and booking from the search cache; one holiday-calendar request",
    )
    parser.add_argument(
        "--photos-only",
        action="store_true",
        help="Fetch the newest 50 餐牌, 環境, and 食物 photos into data/{district}/photos",
    )
    parser.add_argument("--shard", type=int, default=0, help="This shard index, from 0")
    parser.add_argument("--shards", type=int, default=1, help="How many photo shards are running")
    parser.add_argument("--district", action="append", default=[], help="District slug, repeatable. Default: tst")
    parser.add_argument(
        "--fill-covers",
        action="store_true",
        help="Fill empty door photos from stored 環境, then 餐牌, then 食物",
    )
    args = parser.parse_args()
    if args.check:
        self_check()
        return
    if args.fill_covers:
        fill_missing_covers()
        return
    slugs = args.district or ["tst"]
    if args.shards > 1:
        OPENRICE.gap = max(OPENRICE.gap, 0.45)
    for slug in slugs:
        apply_district(slug)
        if args.photos_only:
            patch_photos(
                min(8, max(1, args.workers)),
                use_cache=not args.force,
                shard=args.shard,
                shards=max(1, args.shards),
            )
            continue
        if args.hours_only:
            patch_hours(use_cache=not args.force)
            continue
        workers = min(8, max(1, args.workers))
        refresh(args.target, args.max_scan, workers, use_cache=not args.force)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
