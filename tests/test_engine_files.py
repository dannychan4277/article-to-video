"""內嵌引擎隨外掛附帶的授權與出處。"""

import re

from article_to_video.clips import ENGINE_DIR, FONTS_DIR


def test_engine_keeps_huashu_mit_license_and_credit():
    license_text = (ENGINE_DIR / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in license_text
    assert "alchaincyf (花叔" in license_text

    notice = (ENGINE_DIR / "NOTICE.md").read_text(encoding="utf-8")
    assert "花叔" in notice
    assert "github.com/alchaincyf/huashu-art-motion" in notice


def test_every_bundled_font_has_a_license_entry():
    assert "SIL OPEN FONT LICENSE Version 1.1" in (FONTS_DIR / "OFL.txt").read_text(encoding="utf-8")
    licenses = (FONTS_DIR / "LICENSES.md").read_text(encoding="utf-8")
    fonts_js = (ENGINE_DIR / "lib" / "fonts.js").read_text(encoding="utf-8")

    referenced = set(re.findall(r"lib/fonts/([\w.-]+\.woff)", fonts_js))
    bundled = {p.name for p in FONTS_DIR.glob("*.woff")}

    assert referenced == bundled  # fonts.js 列的檔都在，也沒多帶沒用的
    for name in bundled:
        assert f"`{name}`" in licenses, f"{name} 沒列在 LICENSES.md"
