"""
로고.

assets/logo.png 를 넣으면 그것을 쓰고, 없으면 기본 마크를 그린다.
파일이 없다고 화면이 깨지지 않게 하려는 것이다. 로고를 아직 준비하지 못했거나
파일 이름을 잘못 올려도 로그인 화면은 정상으로 뜬다.

쓸 수 있는 파일: assets/logo.png · .jpg · .jpeg · .webp · .svg
"""
from __future__ import annotations

import base64
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "assets"

# 위에 있는 것부터 찾는다. 같은 이름이 여럿이면 앞의 것이 이긴다.
CANDIDATES = ("logo.svg", "logo.png", "logo.webp", "logo.jpg", "logo.jpeg")

MIME = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

MAX_BYTES = 400_000  # 이보다 큰 파일은 화면이 느려진다


def find_logo() -> Path | None:
    """올려둔 로고 파일을 찾는다. 없으면 None."""
    for name in CANDIDATES:
        path = ASSETS / name
        if path.is_file() and path.stat().st_size <= MAX_BYTES:
            return path
    return None


def _data_uri(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{MIME.get(path.suffix.lower(), 'image/png')};base64,{encoded}"


def fallback_mark(size: int = 68) -> str:
    """
    로고 파일이 없을 때 쓰는 기본 마크.

    식품·생활용품 도매를 다루는 곳이니 쌓아둔 상자로 잡았다.
    글자는 넣지 않는다. 바로 아래에 상호가 이미 적혀 있어서 겹친다.
    """
    return f"""
<svg width="{size}" height="{size}" viewBox="0 0 68 68" fill="none"
     xmlns="http://www.w3.org/2000/svg" role="img" aria-label="태오상사">
  <rect x="6" y="30" width="26" height="24" rx="2" fill="#0F5C4A"/>
  <rect x="36" y="30" width="26" height="24" rx="2" fill="#0F5C4A" opacity="0.55"/>
  <rect x="21" y="12" width="26" height="24" rx="2" fill="#0F5C4A" opacity="0.8"/>
  <path d="M34 12v24" stroke="#FFFFFF" stroke-width="2.5" opacity="0.9"/>
  <path d="M19 30v24" stroke="#FFFFFF" stroke-width="2.5" opacity="0.9"/>
  <path d="M49 30v24" stroke="#FFFFFF" stroke-width="2.5" opacity="0.9"/>
</svg>"""


def logo_markup(width: int = 96) -> str:
    """가운데 정렬된 로고 HTML. 로그인 화면에 그대로 넣는다."""
    logo = find_logo()
    if logo is None:
        inner = fallback_mark()
    else:
        inner = (f'<img src="{_data_uri(logo)}" alt="태오상사" '
                 f'style="max-width:{width}px;max-height:{width}px;'
                 f'width:auto;height:auto;display:block;margin:0 auto;">')
    return f'<div style="text-align:center;margin:0 0 14px;">{inner}</div>'
