#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公告抓取脚本：移动 31 省公告（永久存档，只增不减）+ 联通公告

用法:
    python3 scripts/announce_fetch.py --move-dir site/data --uni-dir unicom/data
说明:
    - 移动 31 省公告列表/详情均为静态 JSON，GET 直取；各省路径只差
      省份缩写与区号，栏目尾部（5004505_873_2361）全站一致。
      详情接口触发 TLS legacy renegotiation，需注入 openssl_legacy.cnf(见下)。
    - 所有抓到的公告都作为本地永久数据保留：只按 id 追加新条目，
      不因为源站删除/下架而从存档里删除存量条目。
    - 联通公告列表/详情为 POST 接口，需带 XHR 特征头(否则中文被替换成 ?)。
    - 输出文件:
        <move-dir>/announce.json          移动公告索引（各省最新在架条目，不含正文）
        <move-dir>/announce/<section>.json 各省全量公告存档（只增不减，6 条正文缓存）
        <uni-dir>/announce.json           联通站公告数据
"""
import os
import re
import json
import sys
import time
import datetime

# 源站(移动网关) TLS legacy renegotiation 兼容(与 main.py 同款处理)
_SSL_CNF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mobile", "openssl_legacy.cnf")
if os.path.exists(_SSL_CNF):
    os.environ.setdefault("OPENSSL_CONF", _SSL_CNF)

import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
MAX_KEEP = 15     # 联通保留条数
MOVE_KEEP = 6     # 移动每省缓存正文的最新公告条数（全部条目只存标题+链接）
FETCH_WORKERS = 3 # 同时抓取的省份数，避免把源站打出限流

# ---------------- 移动(31 省) ----------------
MOVE_BASE = ("https://www.10086.cn/aboutus/news/pannounce/"
             "{abbr}/{code}/5004505_873_2361")
MOVE_DETAIL_PAGE = ("https://www.10086.cn/aboutus/news/pannounce/"
                    "{abbr}/index_{code}_{code}_detail_{id}.html")
MOVE_SITE = "https://www.10086.cn"

# (section, 区号, 官网路径缩写, 省名)；缩写取自 www.10086.cn 首页省份入口
MOVE_PROVINCES = [
    ("beijing", "100", "bj", "北京"),
    ("tianjin", "220", "tj", "天津"),
    ("hebei", "311", "he", "河北"),
    ("shanxi", "351", "sx", "山西"),
    ("neimenggu", "471", "nm", "内蒙古"),
    ("liaoning", "240", "ln", "辽宁"),
    ("jilin", "431", "jl", "吉林"),
    ("heilongjiang", "451", "hl", "黑龙江"),
    ("shanghai", "210", "sh", "上海"),
    ("jiangsu", "250", "js", "江苏"),
    ("zhejiang", "571", "zj", "浙江"),
    ("anhui", "551", "ah", "安徽"),
    ("fujian", "591", "fj", "福建"),
    ("jiangxi", "791", "jx", "江西"),
    ("shandong", "531", "sd", "山东"),
    ("henan", "371", "ha", "河南"),
    ("hubei", "270", "hb", "湖北"),
    ("hunan", "731", "hn", "湖南"),
    ("guangdong", "200", "gd", "广东"),
    ("guangxi", "771", "gx", "广西"),
    ("hainan", "898", "hi", "海南"),
    ("chongqing", "230", "cq", "重庆"),
    ("sichuan", "280", "sc", "四川"),
    ("guizhou", "851", "gz", "贵州"),
    ("yunnan", "871", "yn", "云南"),
    ("xizang", "891", "xz", "西藏"),
    ("shaanxi", "290", "sn", "陕西"),
    ("gansu", "931", "gs", "甘肃"),
    ("qinghai", "971", "qh", "青海"),
    ("ningxia", "951", "nx", "宁夏"),
    ("xinjiang", "991", "xj", "新疆"),
]

# ---------------- 联通 ----------------
UNI_LIST_URL = "https://www.10010.com/mall/service/query/announcementquery"
UNI_DETAIL_URL = "https://www.10010.com/mall/service/query/announcementquerydetail"
UNI_PROVINCE = "031"  # 与 https://www.10010.com/wt_links/index.html#/announcementPage 一致
UNI_DETAIL_PAGE = ("https://www.10010.com/wt_links/index.html#/announcementDetail?announcementId={id}"
                   "&pageSize=12&pageNo=1")
UNI_IMG_HOST = "https://m1.img.10010.com"

UNI_HDRS = {
    "User-Agent": UA,
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": "https://www.10010.com/wt_links/index.html",
    "Origin": "https://www.10010.com",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 富文本中需要保留的基础标签
KEEP_TAGS = {"p", "br", "div", "span", "a", "img", "strong", "b", "em", "i", "u",
             "ul", "ol", "li", "table", "tr", "td", "th", "tbody", "thead", "h1",
             "h2", "h3", "h4", "blockquote"}


def _http_get(url, headers=None, timeout=30, retries=5):
    """源站(移动网关) TLS 握手偶发 UNSAFE_LEGACY_RENEGOTIATION_DISABLED，
    单次失败很常见。原先没有重试 → fetch_prov() 直接抛异常 → 上层 || true
    吞掉 → 公告长期停在旧日期而无任何报错。这里必须重试。"""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
            return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
            print("    [重试 %d/%d] %s" % (i + 1, retries, str(e)[:60]))
            time.sleep(3)
    raise last


def _http_post(url, form, headers=None, timeout=30):
    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers=headers or {"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")


def strip_style(html):
    """去掉富文本中的 mso-*/font-* 等内联样式，保留 color / background / 基础的排版值"""
    def _fix(m):
        attrs = m.group(1)
        keep = []
        for kv in re.findall(r'([\w-]+)\s*:\s*([^;]+);?', attrs):
            k, v = kv[0].strip().lower(), kv[1].strip()
            if not k or not v:
                continue
            lower = (k + ":" + v.lower())
            if k in ("color", "vertical-align", "text-align", "background-color"):
                keep.append(f"{k}:{v}")
            elif v.startswith("#") and k.startswith("backg"):
                keep.append(f"{k}:{v}")
            elif k.startswith("background") and len(v) > 4:
                keep.append(f"{k}:{v}")
        return ' style="' + ";".join(keep) + '"' if keep else ""
    return re.sub(r'\sstyle="([^"]*)"', _fix, html)


def _safe_proto(url):
    """协议白名单：只放行 http/https/mailto 与相对链接，其余(javascript:/data: 等)置空。

    防止抓来的公告正文里夹带 javascript: 或 data:text/html 造成 XSS。
    """
    if not url:
        return ""
    u = str(url).strip()
    # 相对链接 / 锚点 / 站内路径：直接放行
    if u.startswith(("/", "#", "./", "../")):
        return u
    low = u.lower()
    for ok in ("http://", "https://", "mailto:"):
        if low.startswith(ok):
            return u
    return ""


def clean_html(raw):
    """清洗 CMS/Word 富文本为简洁 HTML（保留表格/链接/图片），并补全站内相对链接"""
    if not raw:
        return ""
    h = raw
    h = re.sub(r"<\?xml.*?\?>", "", h, flags=re.S)
    h = re.sub(r"<style.*?</style>", "", h, flags=re.S | re.I)
    h = re.sub(r"<script.*?</script>", "", h, flags=re.S | re.I)
    h = re.sub(r"<!--.*?-->", "", h, flags=re.S)
    # 只保留基础标签，其余(如 span 大量嵌套)一并剥掉标签保留文本
    h = re.sub(r"<(?!/?(p|br|div|a|img|strong|b|em|i|u|ul|ol|li|table|tr|td|th|tbody|thead|h\d|blockquote)(\s|/|>))[^>]*>", "", h, flags=re.I)
    h = strip_style(h)
    # 清理空属性与僵尸 span
    h = re.sub(r'\s+(class|id|lang|dir|colspan|rowspan|border|width|height|align|cellpadding|cellspacing)="[^"]*"', "", h, flags=re.I)
    h = re.sub(r"\s+class=['\"][^'\"]*['\"]", "", h, flags=re.I)
    # 补全图片相对路径
    h = re.sub(r'(<img[^>]*?src=)["\'](?!https?://|data:)([^"\']*)["\']',
               lambda m: m.group(1) + '"' + _abs_link(m.group(2), "move") + '"', h, flags=re.I)
    h = re.sub(r'(<a[^>]*?href=)["\'](?!https?://|#|mailto:)([^"\']*)["\']',
               lambda m: m.group(1) + '"' + _abs_link(m.group(2), "move") + '"', h, flags=re.I)
    # 逐段切分，保留换行
    h = h.replace("</p>", "</p>\n").replace("<br>", "<br>\n").replace("<br/>", "<br>\n").replace("<br />", "<br>\n")
    h = re.sub(r"\n{3,}", "\n\n", h)
    # 链接/图片协议白名单兜底：仅保留 http/https/mailto 与相对链接，其余(如 javascript:/data:text)置空
    h = re.sub(r'(<a[^>]*?href=)["\']([^"\']*)["\']',
               lambda m: m.group(1) + '"' + _safe_proto(m.group(2)) + '"', h, flags=re.I)
    h = re.sub(r'(<img[^>]*?src=)["\']([^"\']*)["\']',
               lambda m: m.group(1) + '"' + _safe_proto(m.group(2)) + '"', h, flags=re.I)
    return h.strip()


_img_dom = {"move": "https://www.10086.cn", "uni": UNI_IMG_HOST}
def _abs_link(path, kind):
    if path.startswith(("http://", "https://", "data:")):
        return path
    if path.startswith("/"):
        if kind == "move":
            return MOVE_SITE + path
        return UNI_IMG_HOST + path
    return path


def html_to_text(h):
    """提取富文本纯文本(用于摘要)"""
    t = re.sub(r"<br\s*/?>", "\n", h, flags=re.I)
    t = re.sub(r"</(p|div|tr|li|h\d)>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = htmlmod_unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def htmlmod_unescape(s):
    import html as _h
    return _h.unescape(s)


def find_attachments(content_html, site):
    """从正文中提取附件(下载链接)"""
    out = []
    for m in re.finditer(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', content_html, flags=re.S | re.I):
        href, name_raw = m.group(1), m.group(2)
        if "/uploadBaseDir/" in href or re.search(r'\.(xlsx?|pdf|docx?|zip|rar|png|jpg|jpeg)(\?|$)', href, flags=re.I):
            name = html_to_text(name_raw).strip() or os.path.basename(href.split("?")[0]) or "附件"
            link = href if href.startswith("http") else (site + href)
            if link not in [a["url"] for a in out]:
                out.append({"name": name[:60], "url": link})
    return out


def now_str():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")


# ---------------- 移动抓取 ----------------
def fetch_list(sec, code, abbr, name):
    """拉取单个省份公告列表（源站当前在架的全部条目）。"""
    base = MOVE_BASE.format(abbr=abbr, code=code)
    j = json.loads(_http_get(base + ".json"))
    lst = ((j.get("cData") or {}).get("list") or [])
    items, seen = [], set()
    for row in lst:
        m = re.search(r"_detail_(\d+)\.", row.get("detail_href") or "")
        if not m:
            continue
        mid = m.group(1)
        if mid in seen:
            continue
        seen.add(mid)
        items.append({
            "id": mid,
            "title": row.get("noticeTitle") or "",
            "date": row.get("publishTime") or "",
            "page_url": MOVE_DETAIL_PAGE.format(abbr=abbr, code=code, id=mid),
        })
    return items


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _detail_fields(base, it):
    """抓一条公告的正文/摘要/附件。"""
    dj = json.loads(_http_get(base + "_detail_" + str(it["id"]) + ".json"))
    c = ((dj.get("cData") or {}).get("content") or {})
    content = clean_html(c.get("text") or "")
    return {
        "summary": html_to_text(content)[:120],
        "content": content,
        "attachments": find_attachments(content, MOVE_SITE),
    }


def _sync_prov_task(p, prov_dir):
    """抓一省列表并追加到本地存档：只增不减，不跟随源站删除。

    已存档的条目原样保留（标题/日期/正文不再改写），只把本地没有的 id 追加进来；
    源站删掉或下架公告后，已抓过的条目仍留在存档里。本轮抓取失败则不动存档。
    """
    sec, code, abbr, name = p
    path = os.path.join(prov_dir, sec + ".json")
    old = _load_json(path) or {}
    now = now_str()
    try:
        cur = fetch_list(sec, code, abbr, name)
    except Exception as e:
        print(f"[移动] {name} 抓取失败: {str(e)[:120]}")
        return sec, name, None, str(e)

    old_map = {}
    for x in (old.get("items") or []):
        mid = str(x.get("id") or "")
        if mid:
            old_map[mid] = x

    merged = list(old_map.values())
    added = 0
    for it in cur:
        mid = str(it.get("id"))
        if mid in old_map:
            continue
        rec = {"id": mid, "title": it["title"], "date": it["date"],
               "page_url": it["page_url"], "status": "on", "first_seen": now}
        old_map[mid] = rec
        merged.append(rec)
        added += 1
    merged.sort(key=lambda x: (x.get("date") or "", str(x.get("id") or "")), reverse=True)

    # 只补抓最新条目中尚无正文的，已有正文不重复请求
    base = MOVE_BASE.format(abbr=abbr, code=code)
    for it in merged[:MOVE_KEEP]:
        if it.get("content"):
            continue
        try:
            it.update(_detail_fields(base, it))
        except Exception as e:
            print(f"[移动] {name} 详情 {it['id']} 失败: {e}")
            it.setdefault("summary", "")
            it.setdefault("content", "")
            it.setdefault("attachments", [])
        time.sleep(0.4)
    print(f"[移动] {name} 新增 {added} 条，本地累计 {len(merged)} 条")
    return sec, name, merged, None


def write_mobile(out_dir):
    """抓取 31 省公告，写全量存档 + 汇总索引。"""
    prov_dir = os.path.join(out_dir, "announce")
    os.makedirs(prov_dir, exist_ok=True)
    results = []
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as ex:
        futs = [ex.submit(_sync_prov_task, p, prov_dir) for p in MOVE_PROVINCES]
        for f in futs:
            results.append(f.result())

    flat, summary = [], []
    for sec, name, items, _err in results:
        path = os.path.join(prov_dir, sec + ".json")
        old = _load_json(path) or {}
        old_items = old.get("items") or []
        if items is None:
            items = old_items
            print(f"[移动] {name} 沿用上次数据 {len(items)} 条")
        else:
            # 内容没变就不重写：存档每小时重写一遍会不断新增 blob，
            # 31 省全量存档累积后会把仓库撑大
            if items != old_items:
                write_json(items, path, {"section": sec, "name": name,
                                         "active": len(items), "removed": 0})
                print(f"[移动] {name} 本地累计 {len(items)} 条")
            else:
                print(f"[移动] {name} 无变化（本地 {len(items)} 条）")
        d = _load_json(path) or {}
        items = d.get("items") or []
        summary.append({"section": sec, "name": name, "count": len(items),
                        "active": len(items), "removed": 0,
                        "updated": d.get("updated") or ""})
        for it in items[:MOVE_KEEP]:
            flat.append({
                "id": it.get("id") or "",
                "prov": sec,
                "prov_name": name,
                "title": it.get("title") or "",
                "date": it.get("date") or "",
                "summary": (it.get("summary") or "")[:80],
                "page_url": it.get("page_url") or "",
                "status": "on",
            })
    flat.sort(key=lambda x: (x.get("date") or "", x.get("id") or ""), reverse=True)
    write_json(flat, os.path.join(out_dir, "announce.json"), {"provinces": summary})
    ok = sum(1 for r in results if r[2] is not None)
    print(f"[移动] 完成：{ok}/{len(results)} 省抓取成功，索引 {len(flat)} 条")


# ---------------- 联通抓取 ----------------
def fetch_uni():
    print("[联通] 拉取公告列表 ...")
    raw = _http_post(UNI_LIST_URL, {"pageNo": "1", "pageSize": str(MAX_KEEP + 5),
                                    "province": UNI_PROVINCE, "condition": "0", "title": ""}, UNI_HDRS)
    j = json.loads(raw)
    lst = j.get("result") or []
    items = []
    for row in lst:
        items.append({
            "id": row.get("id") or "",
            "title": row.get("title") or "",
            "date": (row.get("publishTime") or "")[:10],
            "page_url": UNI_DETAIL_PAGE.format(id=row.get("id") or ""),
        })
    items = [x for x in items if x["id"]][:MAX_KEEP]
    print(f"[联通] 列表 {len(lst)} 条，取最新 {len(items)} 条")
    for it in items:
        try:
            dj = json.loads(_http_post(UNI_DETAIL_URL, {
                "announcementId": it["id"], "pageSize": "12", "pageNo": "1",
                "province": UNI_PROVINCE}, UNI_HDRS))
            n = dj.get("tNoticeDto") or {}
            text = n.get("noticeDetails") or ""
            content = clean_html(text)
            content = re.sub(r'(<img[^>]*?src=)["\'](?!https?://)([^"\']*)["\']',
                             lambda m: m.group(1) + '"' + UNI_IMG_HOST + m.group(2) + '"', content, flags=re.I)
            it["title"] = n.get("title") or it["title"]
            it["date"] = (n.get("noticeTime") or it["date"])[:10]
            it["summary"] = html_to_text(content)[:120]
            it["content"] = content
            it["attachments"] = find_attachments(content, UNI_IMG_HOST)
        except Exception as e:
            print(f"[联通] 详情 {it['id']} 失败: {e}")
            it["summary"], it["content"], it["attachments"] = "", "", []
        time.sleep(0.4)
    return items


def write_json(items, path, extra=None):
    data = {"updated": now_str(), "count": len(items), "items": items}
    if extra:
        data.update(extra)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"已写入 {path} ({len(items)} 条)")


def _resolve(root, p):
    return p if os.path.isabs(p) else os.path.join(root, p)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    args = sys.argv[1:]
    move_dir = uni_dir = None
    for i, a in enumerate(args):
        if a == "--move-dir" and i + 1 < len(args):
            move_dir = _resolve(root, args[i + 1])
        if a == "--uni-dir" and i + 1 < len(args):
            uni_dir = _resolve(root, args[i + 1])
    if move_dir:
        write_mobile(move_dir)
    if uni_dir:
        write_json(fetch_uni(), os.path.join(uni_dir, "announce.json"))
    if not move_dir and not uni_dir:
        print(__doc__)


if __name__ == "__main__":
    main()
