# 尖沙咀 午餐餐廳

尖沙咀 OpenRice 全部餐廳（`districtId=2008`）。店面、餐牌、環境、食物相片都連到 OpenRice，不放圖片檔進這個倉庫。頁面先讀 `data/districts.json`，再讀勾選地區的 `data/{district}/restaurants.json`。沒有勾選地區時讀全部地區。搜尋列在頁面最上方。頁尾是手動更新時間（`資料更新：YYYY-MM-DD HH:mm HKT`）。

網站：https://burnxwallpaper.github.io/tst-restaurants/

## 開啟

GitHub Pages 由 `main` 根目錄發布。打開上面的網址即可。

本機預覽（`file://` 會擋住 `fetch`）：

```bash
python -m http.server 8080
```

然後開 http://localhost:8080/

## 資料

`data/districts.json` 列出地區（`id`、名稱、OpenRice `openrice_district_id`、預設距離起點）。每個地區的餐廳在 `data/{district}/restaurants.json`，形狀是 `{ "meta", "restaurants" }`。距離不寫進 JSON，由頁面用緯度經度現算。

`meta` 包含 `updated_at`（香港時間，手動跑腳本才會變）、`card_count`、`menu_count` / `menu_image_count`、`environment_count` / `environment_image_count`、`food_count` / `food_image_count`、`hours_count`、`bookable_count`、`opened_count`、`public_holidays`（1823 香港公眾假期，`YYYY-MM-DD`）。

每間餐廳有名稱、菜式、人均價錢、地址、`lat` / `lng`、`district`、OpenRice 好評／負評、店面圖網址 `cover`（OpenRice 門口相，沒有則為空字串）、`photo_counts`（`menu`、`environment`、`food`，是 OpenRice 總數）、`hours`、`bookable`、`booking_url`、`opened_on`。相片清單不放在這份主檔。有相片的餐廳另有 `data/{district}/photos/{poiId}.json`，三個陣列 `menu`、`environment`、`food` 各最多最新 50 張，並有 `menu_total`、`environment_total`、`food_total`。每張有 `full`、`time`（OpenRice `submitTime`）、可選的 `caption` 和 `source`。縮圖是把 `full` 的尺寸碼換成 `sx`。卡片地址連到 Google 地圖（有座標用緯度經度，否則用名稱加地址）。「Google 評分」連到以名稱加地址搜尋的 Google 地圖，評分不寫進 JSON。

`hours` 沒有資料時是 `null`。有資料時：

- `week`：鍵 `"0"`（星期日）至 `"6"`（星期六），值是當日的 `[開放, 結束]` 列表（24 小時制 `HH:MM`）。一天可有多段（例如午市、晚市）。`24:00` 表示午夜收市。結束早於開始表示跨夜。空陣列是該日休息。
- `holiday` / `holiday_eve`：公眾假期、公眾假期前夕的時段；沒有這條規則時是 `null`，整日休息是 `[]`。
- `specials`：指定日期的例外（`from`、`to`、`ranges`、`note`）。
- `monthly`：每月第 N 個星期幾的例外。
- `text`：上述內容的中文原文。

營業狀態在頁面用香港時間，由已儲存的營業時間計算（`openingStatus(hours, date)`），不跟觀看者的時區。餐廳資料本身不會自動更新。綠點是營業中，黃點是 60 分鐘內準備營業，紅點是休息中，灰點是未有營業時間。

「篩選」收合時只顯示按鈕（有篩選時帶數量）。打開後左邊是分類（地區、菜式、價錢、距離、營業狀態、訂座、餐牌、開張日期），右邊只顯示目前分類的選項。手機上是全螢幕，左邊約三成寬。同一組多選是「或」；一組都沒勾選就不過濾該組，地區沒勾選會載入全部地區。距離多選時用最遠的範圍。開張日期有「此日期或之後」和「此日期或之前」，留空代表該端不限。網址會記住篩選（`district`、`status`、`booking`、`menu`、`distance`、`price`、`cuisine`、`opened`、`opened_to`）。空的參數和沒有參數一樣。「清除」會清掉勾選、搜尋和日期，排序和起點保持不變。「完成」只關上篩選。相簿網址用 `#album=`、`cat=`、`photo=`，瀏覽器返回會先關燈箱再關相簿。

`bookable` 為 true 代表 OpenRice 有 TableMap 訂座頁，而且訂座沒有被關掉。卡片的「可訂座」連到 `booking_url`。`useExpandLayoutBooking` 每間都是 true，不當成可訂座。

餐牌、環境、食物來自 OpenRice 相片列表：`photoTypeId` 7 是餐牌、2 是環境、1 是食物。每一類每間只留最新 50 張（一次請求），按鈕上的數字仍是 OpenRice 的總數；超過 50 張時相簿會寫「顯示最新 50 張，更多請到 OpenRice」。縮圖由 `full` 的尺寸碼換成 `sx`。相簿是三欄正方形，可切換分類，並可在「最新」和「最舊」之間排序。點縮圖開燈箱（完整圖片不裁切），關閉燈箱回到相簿。沒有該類相片的按鈕是停用的 `(0)`。

## 距離起點

預設起點是美麗華廣場一期（尖沙咀彌敦道 132 號，22.301111, 114.172222）。只選另一個地區、而且網址沒有指定起點時，改用該區港鐵站。起點寫在 `index.html` 的 `ORIGINS` 和 `data/districts.json`。也可以：

- `?origin=mira-place-1`
- `?lat=22.30&lng=114.17`
- 按鈕「使用我的位置」

## 更新並推上網站

在專案根目錄：

```bash
pip install pillow
python scripts/refresh.py
git add data/districts.json data/tst index.html README.md scripts/refresh.py
git commit -m "Update Tsim Sha Tsui restaurants"
git push origin main
```

`refresh.py --district tst` 會抓該區餐廳、引用店面和三類相片網址、並從搜尋結果的 `poiHours` 寫入營業時間。中斷後再跑會沿用 `_cache/`。不加 `--district` 時預設尖沙咀。地區代碼：`tst` 尖沙咀、`pe` 太子、`mk` 旺角、`ssp` 深水埗、`jordan` 佐敦、`ymt` 油麻地、`csw` 長沙灣、`lck` 荔枝角。同一間餐廳出現在多過一個地區時，頁面只顯示一次；每個地區的清單本身仍然完整。距離起點預設跟第一個選中的地區（尖沙咀是美麗華，其他是該區港鐵站），網址有 `origin` 或 `lat`/`lng` 時沿用該起點。

```bash
python scripts/refresh.py --workers 8
python scripts/refresh.py --photos-only
python scripts/refresh.py --hours-only
python scripts/refresh.py --force
python scripts/refresh.py --check
```

`--photos-only` 只抓三類相片各最新 50 張並寫入 `data/{district}/photos/` 同 `photo_counts`，不會重抓店面。`--hours-only` 只從已下載的搜尋快取補上營業時間和訂座，並向 1823 要一次公眾假期日曆。

Google 評分不寫進資料。卡片的「Google 評分」和地址在瀏覽器組 Google 地圖連結。

## 抓取頻率

請隔一段時間再跑。OpenRice 有每秒數次的上限，預設最多 8 個並行，請求之間仍會停一停。營業時間來自搜尋結果，不逐間打開餐廳頁。
