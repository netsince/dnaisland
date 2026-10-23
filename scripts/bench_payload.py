"""看这些接口的响应体积由什么构成（base64 内嵌图片？长文本？）。"""
import gzip
import json
import re
import urllib.request

BASE = "https://dnaisland.nb6.ltd"
UA = "DNAISLAND-bench/1.0"
TARGETS = [
    ("/api/v1/cards/featured", "data"),
    ("/api/v1/cards/explore", "data"),
    ("/api/v1/teahouse/posts", "data"),
    ("/api/v1/sponsors", "data"),
    ("/api/v1/recommend", "data"),
]

def get(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
        return raw

for path, _key in TARGETS:
    try:
        raw = get(path)
    except Exception as exc:
        print(path, "失败", str(exc)[:60])
        continue
    text = raw.decode("utf-8", "replace")
    total = len(raw)
    # base64 内嵌图片
    imgs = re.findall(r'data:image/[a-zA-Z+]+;base64,([A-Za-z0-9+/=]+)', text)
    img_bytes = sum(len(m) for m in imgs)
    # 最长的单个字符串字段（可能是正文）
    try:
        obj = json.loads(text)
    except Exception:
        obj = None
    print(f"{path:<28} 总 {total / 1024.0:>7.1f}KB  data:image x{len(imgs):<3} 占 {img_bytes / 1024.0:>7.1f}KB ({100.0 * img_bytes / total if total else 0:.0f}%)")
