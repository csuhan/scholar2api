"""Captcha solver interface."""
from concurrent.futures import ThreadPoolExecutor
from typing import List


class CaptchaSolver:
    """Verifies whether an image tile contains a target object."""

    name = "base"

    def ask_image(self, image_png: bytes, question: str) -> str:
        raise NotImplementedError

    def available(self) -> bool:
        return True

    def select_tiles(self, tiles_png: List[bytes], target: str) -> List[int]:
        def ask(item):
            index, tile = item
            try:
                answer = self.ask_image(
                    tile, f"Does this image contain {target}? "
                          "Answer strictly yes or no.")
                return index, answer.strip().lower().startswith("yes")
            except Exception:
                return index, False

        with ThreadPoolExecutor(max_workers=4) as pool:
            return [i for i, hit in pool.map(ask, enumerate(tiles_png)) if hit]
