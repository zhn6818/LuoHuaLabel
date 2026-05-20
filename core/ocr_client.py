# -*- coding: utf-8 -*-
import numpy as np
import cv2
from PIL import Image
from PySide6.QtCore import QObject, QThread, Signal

try:
    from paddleocr import PaddleOCR
except ImportError:
    PaddleOCR = None


class OCRWorker(QThread):
    result_ready = Signal(list, bool, str)

    def __init__(self):
        super().__init__()
        self._ocr = None
        self.image_path = None
        self.crop_region = None

    def init_ocr(self):
        if PaddleOCR is None:
            return False
        try:
            self._ocr = PaddleOCR(
                use_angle_cls=True,
                lang='ch',
                use_gpu=False,
                show_log=False,
            )
            return True
        except Exception as e:
            print(f"OCR 初始化失败: {e}")
            return False

    def run(self):
        if not self._ocr:
            if not self.init_ocr():
                self.result_ready.emit([], False, "OCR 引擎初始化失败，请检查 paddleocr 是否正确安装")
                return

        try:
            img = cv2.imread(self.image_path)
            if img is None:
                self.result_ready.emit([], False, "无法读取图片")
                return

            if self.crop_region:
                x, y, w, h = self.crop_region
                x1 = max(0, int(x))
                y1 = max(0, int(y))
                x2 = min(img.shape[1], int(x + w))
                y2 = min(img.shape[0], int(y + h))
                if x2 <= x1 or y2 <= y1:
                    self.result_ready.emit([], False, "裁剪区域无效")
                    return
                img = img[y1:y2, x1:x2]

            result = self._ocr.ocr(img, cls=True)

            ocr_results = []
            if result and result[0]:
                for line in result[0]:
                    box = line[0]
                    text = line[1][0]
                    confidence = line[1][1]

                    if self.crop_region:
                        ox, oy = self.crop_region[0], self.crop_region[1]
                        box = [[p[0] + ox, p[1] + oy] for p in box]

                    xs = [p[0] for p in box]
                    ys = [p[1] for p in box]
                    min_x, max_x = min(xs), max(xs)
                    min_y, max_y = min(ys), max(ys)

                    ocr_results.append({
                        "text": text,
                        "confidence": confidence,
                        "box": box,
                        "rect": [min_x, min_y, max_x - min_x, max_y - min_y],
                    })

            self.result_ready.emit(ocr_results, True, f"识别完成，发现 {len(ocr_results)} 个文本区域")

        except Exception as e:
            self.result_ready.emit([], False, f"OCR 识别错误: {e}")


class OCRClient(QObject):
    ocr_result_ready = Signal(list, bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = OCRWorker()
        self.worker.result_ready.connect(self.ocr_result_ready)

    def recognize(self, image_path, crop_region=None):
        if self.worker.isRunning():
            self.worker.wait(3000)
        self.worker.image_path = image_path
        self.worker.crop_region = crop_region
        self.worker.start()

    def cleanup(self):
        if self.worker.isRunning():
            self.worker.wait(3000)
