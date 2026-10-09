"""配樂：用內嵌引擎的程式作曲範本（engine/audio/synth_template.py）的做法，產生幾首原創配樂。

（合成在後面的切片補上。）
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["TRACKS", "Track"]


@dataclass(frozen=True)
class Track:
    name: str
    label: str
    """給作者看的名字。"""
    mood: str
    """一句話說明（安裝時試聽用）。"""


TRACKS: dict[str, Track] = {
    "calm": Track("calm", "沉穩鋼琴", "鋼琴配柔和鋪底，慢而穩，適合分析型內容"),
    "pulse": Track("pulse", "新聞脈動", "撥弦配輕鼓點，有往前推的感覺"),
    "minimal": Track("minimal", "極簡電子", "低音配電子撥奏，乾淨俐落"),
}
