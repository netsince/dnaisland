"""动图（GIF）必须保留全部帧，不能被转成静止图。

背景：上游 gpt-image-2.5 在提示词要求动图时会返回 GIF89a。旧实现用
``Image.open(...).save(WEBP)``，Pillow 只写第一帧 —— 用户拿到的「动图」
只剩一张静止图，且没有任何报错。

实测（2026-11）：433x394 / 9 帧 / 760KB 的 GIF，旧实现输出 23KB / 1 帧。
"""

import base64
import io

from PIL import Image

from app.services.image_gen_service import _extract_images
from app.services.image_service import (
    data_url_to_webp_bytes,
    image_bytes_to_webp_data_url,
    sniff_image_mime,
)


def _animated_gif(frames=3, size=(24, 24), duration=80):
    """现场造一个多帧 GIF，不依赖二进制夹具。"""
    imgs = [Image.new("RGBA", size, (i * 60 % 256, 0, 0, 255)) for i in range(frames)]
    buf = io.BytesIO()
    imgs[0].save(
        buf, format="GIF", save_all=True, append_images=imgs[1:],
        duration=duration, loop=0,
    )
    return buf.getvalue()


def _png(size=(24, 24)):
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


def _decode(data_url):
    return base64.b64decode(data_url.split(",", 1)[1])


def _b(hexstr):
    """由十六进制串造字节，避免在源码里写转义字面量。"""
    return bytes.fromhex(hexstr)


def test_sniff_识别常见格式():
    assert sniff_image_mime(_animated_gif()) == "image/gif"
    assert sniff_image_mime(_png()) == "image/png"
    assert sniff_image_mime(_b("ffd8ffe0") + b"rest") == "image/jpeg"
    riff_webp = _b("52494646") + _b("00000000") + b"WEBP" + b"VP8 "
    assert sniff_image_mime(riff_webp) == "image/webp"
    assert sniff_image_mime(_b('00010203')) is None


def test_动图转webp保留全部帧():
    gif = _animated_gif(frames=4)
    url = image_bytes_to_webp_data_url(gif)
    assert url.startswith("data:image/webp;base64,")
    out = Image.open(io.BytesIO(_decode(url)))
    assert getattr(out, "n_frames", 1) == 4, "动图帧数必须全保留"
    assert getattr(out, "is_animated", False) is True


def test_静态图转webp仍是单帧():
    url = image_bytes_to_webp_data_url(_png())
    assert url.startswith("data:image/webp;base64,")
    out = Image.open(io.BytesIO(_decode(url)))
    assert getattr(out, "n_frames", 1) == 1


def test_extract_images_遇到gif也不丢帧():
    """走真实的上游解析入口，而不是只测底层函数。"""
    gif = _animated_gif(frames=3)
    payload = {"data": [{"b64_json": base64.b64encode(gif).decode("ascii")}]}
    out = _extract_images(payload)
    assert len(out) == 1
    assert out[0].startswith("data:image/webp;base64,")
    img = Image.open(io.BytesIO(_decode(out[0])))
    assert getattr(img, "n_frames", 1) == 3


def test_库里的gif_data_url在发送时也转成动图webp():
    """存量/旁路写入的 GIF 也必须在服务端补正，不能原样当 webp 发出去。"""
    gif = _animated_gif(frames=3)
    data_url = "data:image/gif;base64," + base64.b64encode(gif).decode("ascii")
    served = data_url_to_webp_bytes(data_url)
    assert sniff_image_mime(served) == "image/webp"
    img = Image.open(io.BytesIO(served))
    assert getattr(img, "n_frames", 1) == 3


def test_已是webp的data_url原样透传不重编码():
    url = image_bytes_to_webp_data_url(_png())
    raw = _decode(url)
    # 命中快路径：字节完全一致（不二次压缩，避免画质损失）
    assert data_url_to_webp_bytes(url) == raw