# -*- coding: utf-8 -*-
from PySide6.QtWidgets import QGraphicsScene, QGraphicsPixmapItem, QGraphicsLineItem, QGraphicsRectItem, QGraphicsEllipseItem
from PySide6.QtGui import QPixmap, QPolygonF, QPen, QColor, QBrush
from PySide6.QtCore import Qt, QPointF, Signal, QRectF
from core.shapes import RectShape, PolyShape, PointShape, RotatedRectShape, HandleItem


class CanvasMode:
    EDIT = 0
    RECT = 1
    POLY = 2
    POINT = 3
    RBOX = 4
    COMBO_RECT = 5
    AIOCR = 6

    @staticmethod
    def get_mode_name(mode):
        names = {1: "矩形", 2: "多边形", 3: "点", 4: "旋转框", 5: "组合矩形", 6: "AI识别"}
        return names.get(mode, "未知")


class Canvas(QGraphicsScene):
    mouse_moved = Signal(int, int)
    shape_drawn = Signal(object)
    shape_double_clicked = Signal(object)
    state_changed = Signal()
    ocr_region_selected = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = CanvasMode.RECT
        self.img_item = None
        self.sam_client = None
        self.sam_enabled = False
        self.ocr_client = None

        self.drawing = False
        self.start_pt = None
        self.temp_item = None
        self.poly_pts = []

        # 智能悬停提示图层
        self.sam_hover_item = None

        # 多点提示收集（右键多点合一）
        self.sam_multi_points = []
        self.sam_point_markers = []

        # 多集合提示（Ctrl+右键，每个点独立推理，合并预览）
        self.sam_multiset_markers = []
        self.sam_multiset_preview_items = []
        self.sam_multiset_results = []

        # 组合矩形：右键加点各自预览，左键合并大矩形
        self.combo_markers = []
        self.combo_preview_items = []
        self.combo_results = []  # [(x, y, w, h), ...]

        self.h_line = QGraphicsLineItem()
        self.v_line = QGraphicsLineItem()
        crosshair_pen = QPen(QColor(255, 255, 255, 200), 1, Qt.DashLine)
        self.h_line.setPen(crosshair_pen)
        self.v_line.setPen(crosshair_pen)
        self.h_line.setZValue(9999)
        self.v_line.setZValue(9999)
        self.h_line.hide()
        self.v_line.hide()
        self.addItem(self.h_line)
        self.addItem(self.v_line)

    def load_image(self, path):
        self.clear_shapes()
        pixmap = QPixmap(path)
        if self.img_item:
            self.removeItem(self.img_item)
        self.img_item = QGraphicsPixmapItem(pixmap)
        self.addItem(self.img_item)
        self.setSceneRect(pixmap.rect())
        self.h_line.show()
        self.v_line.show()

    def clear_shapes(self):
        for item in self.items():
            if isinstance(item, (RectShape, PolyShape, PointShape, RotatedRectShape)):
                self.removeItem(item)

    def set_mode(self, mode):
        self.mode = mode
        self.cancel_drawing()
        for item in self.selectedItems():
            item.setSelected(False)

    def set_sam_enabled(self, enabled):
        self.sam_enabled = enabled
        if not enabled and self.sam_hover_item:
            self.removeItem(self.sam_hover_item)
            self.sam_hover_item = None

    def is_inside_image(self, pt):
        if not self.img_item: return False
        return self.sceneRect().contains(pt)

    def clamp_point(self, pt):
        rect = self.sceneRect()
        x = max(rect.left(), min(pt.x(), rect.right()))
        y = max(rect.top(), min(pt.y(), rect.bottom()))
        return QPointF(x, y)

    def update_crosshair(self, pt):
        if self.img_item:
            rect = self.sceneRect()
            x = max(rect.left(), min(pt.x(), rect.right()))
            y = max(rect.top(), min(pt.y(), rect.bottom()))
            self.h_line.setLine(rect.left(), y, rect.right(), y)
            self.v_line.setLine(x, rect.top(), x, rect.bottom())
            self.mouse_moved.emit(int(x), int(y))

    def mouseMoveEvent(self, event):
        pt = event.scenePos()
        self.update_crosshair(pt)
        super().mouseMoveEvent(event)
        clamped_pt = self.clamp_point(pt)

        # ---------------- SAM 智能辅助悬停 ----------------
        has_points = len(self.sam_multi_points) > 0 or len(self.combo_markers) > 0
        if self.sam_enabled and has_points:
            return
        if self.sam_enabled and self.is_inside_image(pt) and self.mode in [
            CanvasMode.RECT, CanvasMode.POLY, CanvasMode.RBOX, CanvasMode.COMBO_RECT, CanvasMode.AIOCR]:
            if self.sam_client:
                self.sam_client.request_inference(clamped_pt.x(), clamped_pt.y(), is_click=False)
            return
        elif self.sam_hover_item:
            self.removeItem(self.sam_hover_item)
            self.sam_hover_item = None

        # ---------------- 常规绘图 ----------------
        if self.drawing and self.start_pt:
            rect = QRectF(min(self.start_pt.x(), clamped_pt.x()), min(self.start_pt.y(), clamped_pt.y()),
                          abs(clamped_pt.x() - self.start_pt.x()), abs(clamped_pt.y() - self.start_pt.y()))
            if self.temp_item: self.removeItem(self.temp_item)

            if self.mode == CanvasMode.RECT or self.mode == CanvasMode.AIOCR:
                self.temp_item = QGraphicsRectItem(rect)
                self.temp_item.is_temp = True
                self.temp_item.setPen(QPen(QColor(28, 126, 214), 2, Qt.DashLine))

            elif self.mode == CanvasMode.RBOX:
                cx, cy = rect.center().x(), rect.center().y()
                w, h = max(1, rect.width()), max(1, rect.height())
                self.temp_item = RotatedRectShape(cx, cy, w, h, 0, is_temp=True)

            self.addItem(self.temp_item)

        elif self.mode == CanvasMode.POLY and not self.sam_enabled and len(self.poly_pts) > 0:
            self.update_temp_poly(mouse_pos=clamped_pt)

    def handle_sam_result(self, poly_pts, rect_xywh, rect_obb, score, is_click):
        """处理 SAM 单点推理结果"""
        if not self.sam_enabled:
            return

        # ---- 组合矩形模式 ----
        if self.mode == CanvasMode.COMBO_RECT:
            if not is_click and rect_xywh:
                # 悬停预览 + 收集右键加点的结果
                if len(self.combo_markers) > 0:
                    self.combo_results.append(list(rect_xywh))
                    item = QGraphicsRectItem(QRectF(*rect_xywh))
                    item.setPen(QPen(QColor(0, 200, 255), 2, Qt.DashLine))
                    item.setBrush(QBrush(QColor(0, 200, 255, 40)))
                    self.addItem(item)
                    self.combo_preview_items.append(item)
                    # 更新合并大矩形预览
                    self._update_combo_merged_preview()
                    return
            # 普通悬停（还没右键加点时）
            if self.sam_hover_item:
                self.removeItem(self.sam_hover_item)
                self.sam_hover_item = None
            if not poly_pts or not rect_xywh:
                return
            if is_click:
                return
            self.sam_hover_item = QGraphicsRectItem(QRectF(*rect_xywh))
            self.sam_hover_item.setPen(QPen(QColor(0, 255, 0), 2, Qt.DashLine))
            self.sam_hover_item.setBrush(QBrush(QColor(0, 255, 0, 50)))
            self.addItem(self.sam_hover_item)
            return

        # ---- 其他模式 ----
        if self.mode not in [CanvasMode.RECT, CanvasMode.POLY, CanvasMode.RBOX, CanvasMode.AIOCR]:
            return

        # 多集合模式
        is_multiset = len(self.sam_multiset_markers) > 0 and not is_click
        if is_multiset:
            if not poly_pts or not rect_xywh:
                return
            self.sam_multiset_results.append((list(poly_pts), list(rect_xywh), list(rect_obb) if rect_obb else []))
            preview = self._make_preview_shape(poly_pts, rect_xywh, rect_obb, QColor(0, 200, 255))
            if preview:
                self.sam_multiset_preview_items.append(preview)
            return

        if self.sam_hover_item:
            self.removeItem(self.sam_hover_item)
            self.sam_hover_item = None
        if not poly_pts or not rect_xywh:
            return

        if self.mode == CanvasMode.RECT or self.mode == CanvasMode.AIOCR:
            x, y, w, h = rect_xywh
            rect = QRectF(x, y, w, h)
            if is_click:
                if self.mode == CanvasMode.AIOCR:
                    self.ocr_region_selected.emit(RectShape(rect))
                else:
                    self.shape_drawn.emit(RectShape(rect))
            else:
                self.sam_hover_item = QGraphicsRectItem(rect)
                self.sam_hover_item.setPen(QPen(QColor(0, 255, 0), 2, Qt.DashLine))
                self.sam_hover_item.setBrush(QBrush(QColor(0, 255, 0, 50)))
                self.addItem(self.sam_hover_item)

        elif self.mode == CanvasMode.POLY:
            qpts = [QPointF(p[0], p[1]) for p in poly_pts]
            if is_click:
                self.shape_drawn.emit(PolyShape(QPolygonF(qpts)))
            else:
                self.sam_hover_item = PolyShape(QPolygonF(qpts), is_temp=True)
                self.sam_hover_item.setPen(QPen(QColor(0, 255, 0), 2, Qt.DashLine))
                self.sam_hover_item.setBrush(QBrush(QColor(0, 255, 0, 50)))
                self.addItem(self.sam_hover_item)

        elif self.mode == CanvasMode.RBOX:
            if not rect_obb or len(rect_obb) < 5: return
            cx, cy, w, h, angle = rect_obb
            if is_click:
                self.shape_drawn.emit(RotatedRectShape(cx, cy, w, h, angle))
            else:
                self.sam_hover_item = RotatedRectShape(cx, cy, w, h, angle, is_temp=True)
                self.addItem(self.sam_hover_item)

    def handle_multi_point_result(self, poly_pts, rect_xywh, rect_obb, score, is_click):
        """处理多点提示的 SAM 推理结果"""
        if not self.sam_enabled or self.mode not in [CanvasMode.RECT, CanvasMode.POLY, CanvasMode.RBOX, CanvasMode.AIOCR]:
            return
        if self.sam_hover_item:
            self.removeItem(self.sam_hover_item)
            self.sam_hover_item = None
        if not poly_pts or not rect_xywh:
            return

        if self.mode == CanvasMode.RECT or self.mode == CanvasMode.AIOCR:
            x, y, w, h = rect_xywh
            if is_click:
                if self.mode == CanvasMode.AIOCR:
                    self.ocr_region_selected.emit(RectShape(QRectF(x, y, w, h)))
                else:
                    self.shape_drawn.emit(RectShape(QRectF(x, y, w, h)))
            else:
                self.sam_hover_item = QGraphicsRectItem(QRectF(x, y, w, h))
                self.sam_hover_item.setPen(QPen(QColor(0, 255, 0), 2, Qt.DashLine))
                self.sam_hover_item.setBrush(QBrush(QColor(0, 255, 0, 50)))
                self.addItem(self.sam_hover_item)

        elif self.mode == CanvasMode.POLY:
            qpts = [QPointF(p[0], p[1]) for p in poly_pts]
            if is_click:
                self.shape_drawn.emit(PolyShape(QPolygonF(qpts)))
            else:
                self.sam_hover_item = PolyShape(QPolygonF(qpts), is_temp=True)
                self.sam_hover_item.setPen(QPen(QColor(0, 255, 0), 2, Qt.DashLine))
                self.sam_hover_item.setBrush(QBrush(QColor(0, 255, 0, 50)))
                self.addItem(self.sam_hover_item)

        elif self.mode == CanvasMode.RBOX:
            if not rect_obb or len(rect_obb) < 5: return
            cx, cy, w, h, angle = rect_obb
            if is_click:
                self.shape_drawn.emit(RotatedRectShape(cx, cy, w, h, angle))
            else:
                self.sam_hover_item = RotatedRectShape(cx, cy, w, h, angle, is_temp=True)
                self.addItem(self.sam_hover_item)

    def mousePressEvent(self, event):
        pt = event.scenePos()
        clamped_pt = self.clamp_point(pt)

        # ================ 组合矩形模式 ================
        if (self.sam_enabled and self.mode == CanvasMode.COMBO_RECT
                and self.is_inside_image(pt)):
            # 右键：加点 + 单点推理
            if event.button() == Qt.RightButton:
                marker = QGraphicsEllipseItem(clamped_pt.x() - 5, clamped_pt.y() - 5, 10, 10)
                marker.setPen(QPen(QColor(0, 200, 255), 2))
                marker.setBrush(QBrush(QColor(0, 200, 255, 180)))
                marker.setZValue(9998)
                self.addItem(marker)
                self.combo_markers.append(marker)
                if self.sam_client:
                    self.sam_client.request_inference(clamped_pt.x(), clamped_pt.y(), is_click=False)
                return

            # 左键：确认合并大矩形
            if event.button() == Qt.LeftButton and len(self.combo_results) > 0:
                min_x = min(r[0] for r in self.combo_results)
                min_y = min(r[1] for r in self.combo_results)
                max_x = max(r[0] + r[2] for r in self.combo_results)
                max_y = max(r[1] + r[3] for r in self.combo_results)
                self.shape_drawn.emit(RectShape(QRectF(min_x, min_y, max_x - min_x, max_y - min_y)))
                self.clear_combo()
                return

        # ================ SAM Ctrl+右键：多集合独立推理 ================
        if (self.sam_enabled and event.button() == Qt.RightButton
                and event.modifiers() & Qt.ControlModifier
                and self.mode in [CanvasMode.RECT, CanvasMode.POLY, CanvasMode.RBOX, CanvasMode.AIOCR]
                and self.is_inside_image(pt)):
            marker = QGraphicsEllipseItem(clamped_pt.x() - 5, clamped_pt.y() - 5, 10, 10)
            marker.setPen(QPen(QColor(0, 200, 255), 2))
            marker.setBrush(QBrush(QColor(0, 200, 255, 180)))
            marker.setZValue(9998)
            self.addItem(marker)
            self.sam_multiset_markers.append(marker)
            if self.sam_client:
                self.sam_client.request_inference(clamped_pt.x(), clamped_pt.y(), is_click=False)
            return

        # ================ SAM 右键：多点合一预览 ================
        if (self.sam_enabled and event.button() == Qt.RightButton
                and self.mode in [CanvasMode.RECT, CanvasMode.POLY, CanvasMode.RBOX, CanvasMode.AIOCR]
                and self.is_inside_image(pt)):
            self.sam_multi_points.append((clamped_pt.x(), clamped_pt.y()))
            marker = QGraphicsEllipseItem(clamped_pt.x() - 5, clamped_pt.y() - 5, 10, 10)
            marker.setPen(QPen(QColor(255, 200, 0), 2))
            marker.setBrush(QBrush(QColor(255, 200, 0, 180)))
            marker.setZValue(9998)
            self.addItem(marker)
            self.sam_point_markers.append(marker)
            if self.sam_client:
                self.sam_client.request_multi_point_inference(self.sam_multi_points, is_click=False)
            return

        # ================ SAM 左键确认 ================
        if (self.sam_enabled and event.button() == Qt.LeftButton
                and self.mode in [CanvasMode.RECT, CanvasMode.POLY, CanvasMode.RBOX, CanvasMode.AIOCR]):
            # 多集合确认
            if len(self.sam_multiset_results) > 0:
                for poly_pts, rect_xywh, rect_obb in self.sam_multiset_results:
                    shape = self._make_permanent_shape(poly_pts, rect_xywh, rect_obb)
                    if shape:
                        self.shape_drawn.emit(shape)
                self.clear_multiset()
                return
            # 多点合一确认
            if len(self.sam_multi_points) > 0 and self.sam_client:
                self.sam_client.request_multi_point_inference(list(self.sam_multi_points), is_click=True)
                self.clear_multi_points()
                return
            # 单点确认
            if self.is_inside_image(pt) and self.sam_client:
                self.sam_client.request_inference(clamped_pt.x(), clamped_pt.y(), is_click=True)
            return

        # 右键：SAM 模式下拦截
        if event.button() == Qt.RightButton and self.sam_enabled:
            return

        items = self.items(clamped_pt)
        clicked_item = None
        for item in items:
            if isinstance(item, HandleItem) and item.isVisible():
                if not getattr(item.parentItem(), 'is_temp', False):
                    clicked_item = item
                    break
        if not clicked_item:
            for item in items:
                if isinstance(item, (PolyShape, RectShape, PointShape, RotatedRectShape)):
                    if not getattr(item, 'is_temp', False):
                        clicked_item = item
                        break

        if clicked_item and not self.sam_enabled:
            super().mousePressEvent(event)
            if event.button() == Qt.LeftButton:
                if not (event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)):
                    for item in self.selectedItems():
                        if item != clicked_item and item != clicked_item.parentItem():
                            item.setSelected(False)
                    if isinstance(clicked_item, HandleItem):
                        clicked_item.parentItem().setSelected(True)
                    else:
                        clicked_item.setSelected(True)
            return

        # ---------------- 常规绘图起点 ----------------
        if not self.is_inside_image(pt) and not self.drawing: return
        if event.button() == Qt.LeftButton:
            if self.mode in [CanvasMode.RECT, CanvasMode.RBOX, CanvasMode.AIOCR]:
                self.drawing = True
                self.start_pt = clamped_pt
            elif self.mode == CanvasMode.POLY:
                if len(self.poly_pts) > 2:
                    dist = ((clamped_pt.x() - self.poly_pts[0].x()) ** 2 + (
                            clamped_pt.y() - self.poly_pts[0].y()) ** 2) ** 0.5
                    if dist < 10:
                        self.finish_poly_shape()
                        return
                self.poly_pts.append(clamped_pt)
                self.update_temp_poly()
            elif self.mode == CanvasMode.POINT:
                shape = PointShape(clamped_pt)
                self.shape_drawn.emit(shape)
        elif event.button() == Qt.RightButton:
            if self.mode == CanvasMode.POLY and len(self.poly_pts) > 2:
                self.finish_poly_shape()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self.sam_enabled: return

        if event.button() == Qt.LeftButton and self.drawing:
            self.drawing = False
            if self.temp_item:
                pt = self.clamp_point(event.scenePos())
                rect = QRectF(min(self.start_pt.x(), pt.x()), min(self.start_pt.y(), pt.y()),
                              abs(pt.x() - self.start_pt.x()), abs(pt.y() - self.start_pt.y()))
                self.removeItem(self.temp_item)
                self.temp_item = None

                if rect.width() > 5 and rect.height() > 5:
                    if self.mode == CanvasMode.RECT:
                        self.shape_drawn.emit(RectShape(rect))
                    elif self.mode == CanvasMode.RBOX:
                        cx, cy = rect.center().x(), rect.center().y()
                        w, h = rect.width(), rect.height()
                        self.shape_drawn.emit(RotatedRectShape(cx, cy, w, h, 0))
                    elif self.mode == CanvasMode.AIOCR:
                        self.ocr_region_selected.emit(RectShape(rect))

        self.state_changed.emit()

    def mouseDoubleClickEvent(self, event):
        pt = event.scenePos()
        if not self.is_inside_image(pt): return

        for item in self.items(pt):
            if isinstance(item, (HandleItem, PolyShape, RectShape, PointShape, RotatedRectShape)) and not getattr(item,
                                                                                                                  'is_temp',
                                                                                                                  False):
                shape_item = item.parentItem() if isinstance(item, HandleItem) else item
                self.shape_double_clicked.emit(shape_item)
                return

        if event.button() == Qt.LeftButton and self.mode == CanvasMode.POLY and not self.sam_enabled and len(
                self.poly_pts) > 2:
            self.finish_poly_shape()
        else:
            super().mouseDoubleClickEvent(event)

    def update_temp_poly(self, mouse_pos=None):
        display_pts = self.poly_pts.copy()
        if mouse_pos is not None: display_pts.append(mouse_pos)
        if len(display_pts) < 2:
            if self.temp_item: self.removeItem(self.temp_item); self.temp_item = None
            return
        if self.temp_item and isinstance(self.temp_item, PolyShape):
            self.temp_item.setPolygon(QPolygonF(display_pts))
        else:
            if self.temp_item: self.removeItem(self.temp_item)
            self.temp_item = PolyShape(QPolygonF(display_pts), is_temp=True)
            self.addItem(self.temp_item)

    def finish_poly_shape(self):
        shape = PolyShape(QPolygonF(self.poly_pts))
        self.poly_pts.clear()
        if self.temp_item:
            self.removeItem(self.temp_item)
            self.temp_item = None
        self.shape_drawn.emit(shape)

    def cancel_drawing(self):
        self.drawing = False
        self.poly_pts.clear()
        if self.temp_item:
            self.removeItem(self.temp_item)
            self.temp_item = None
        if self.sam_hover_item:
            self.removeItem(self.sam_hover_item)
            self.sam_hover_item = None
        self.clear_multi_points()
        self.clear_multiset()
        self.clear_combo()

    def clear_multi_points(self):
        self.sam_multi_points.clear()
        for m in self.sam_point_markers:
            self.removeItem(m)
        self.sam_point_markers.clear()

    def clear_multiset(self):
        for m in self.sam_multiset_markers:
            self.removeItem(m)
        self.sam_multiset_markers.clear()
        for s in self.sam_multiset_preview_items:
            self.removeItem(s)
        self.sam_multiset_preview_items.clear()
        self.sam_multiset_results.clear()

    def clear_combo(self):
        for m in self.combo_markers:
            self.removeItem(m)
        self.combo_markers.clear()
        for p in self.combo_preview_items:
            self.removeItem(p)
        self.combo_preview_items.clear()
        self.combo_results.clear()
        if hasattr(self, '_combo_merged_preview') and self._combo_merged_preview:
            self.removeItem(self._combo_merged_preview)
            self._combo_merged_preview = None

    def _update_combo_merged_preview(self):
        """更新组合矩形的大矩形合并预览"""
        if hasattr(self, '_combo_merged_preview') and self._combo_merged_preview:
            self.removeItem(self._combo_merged_preview)
            self._combo_merged_preview = None
        if not self.combo_results:
            return
        min_x = min(r[0] for r in self.combo_results)
        min_y = min(r[1] for r in self.combo_results)
        max_x = max(r[0] + r[2] for r in self.combo_results)
        max_y = max(r[1] + r[3] for r in self.combo_results)
        self._combo_merged_preview = QGraphicsRectItem(QRectF(min_x, min_y, max_x - min_x, max_y - min_y))
        self._combo_merged_preview.setPen(QPen(QColor(255, 100, 100), 3, Qt.DashLine))
        self._combo_merged_preview.setBrush(QBrush(QColor(255, 100, 100, 30)))
        self._combo_merged_preview.setZValue(9997)
        self.addItem(self._combo_merged_preview)

    def _make_preview_shape(self, poly_pts, rect_xywh, rect_obb, color):
        pen = QPen(color, 2, Qt.DashLine)
        brush = QBrush(QColor(color.red(), color.green(), color.blue(), 50))
        if self.mode == CanvasMode.RECT or self.mode == CanvasMode.AIOCR:
            x, y, w, h = rect_xywh
            item = QGraphicsRectItem(QRectF(x, y, w, h))
            item.setPen(pen)
            item.setBrush(brush)
            self.addItem(item)
            return item
        elif self.mode == CanvasMode.POLY:
            qpts = [QPointF(p[0], p[1]) for p in poly_pts]
            item = PolyShape(QPolygonF(qpts), is_temp=True)
            item.setPen(pen)
            item.setBrush(brush)
            self.addItem(item)
            return item
        elif self.mode == CanvasMode.RBOX:
            if not rect_obb or len(rect_obb) < 5:
                return None
            cx, cy, w, h, angle = rect_obb
            item = RotatedRectShape(cx, cy, w, h, angle, is_temp=True)
            self.addItem(item)
            return item
        return None

    def _make_permanent_shape(self, poly_pts, rect_xywh, rect_obb):
        if self.mode == CanvasMode.RECT:
            x, y, w, h = rect_xywh
            return RectShape(QRectF(x, y, w, h))
        elif self.mode == CanvasMode.POLY:
            qpts = [QPointF(p[0], p[1]) for p in poly_pts]
            return PolyShape(QPolygonF(qpts))
        elif self.mode == CanvasMode.RBOX:
            if not rect_obb or len(rect_obb) < 5:
                return None
            cx, cy, w, h, angle = rect_obb
            return RotatedRectShape(cx, cy, w, h, angle)
        return None

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()

        if key == Qt.Key_Backspace or key == Qt.Key_Delete:
            for item in self.selectedItems():
                self.removeItem(item)
            self.state_changed.emit()
        elif key == Qt.Key_Z and modifiers == Qt.ControlModifier:
            if self.mode == CanvasMode.POLY and not self.sam_enabled and len(self.poly_pts) > 0:
                self.poly_pts.pop()
                self.update_temp_poly()
        elif key == Qt.Key_Return or key == Qt.Key_Enter:
            # 回车：多集合模式合并大矩形
            if self.sam_enabled and len(self.sam_multiset_results) > 0 and self.mode == CanvasMode.RECT:
                min_x = min(r[1][0] for r in self.sam_multiset_results)
                min_y = min(r[1][1] for r in self.sam_multiset_results)
                max_x = max(r[1][0] + r[1][2] for r in self.sam_multiset_results)
                max_y = max(r[1][1] + r[1][3] for r in self.sam_multiset_results)
                self.shape_drawn.emit(RectShape(QRectF(min_x, min_y, max_x - min_x, max_y - min_y)))
                self.clear_multiset()
                return
            if self.mode == CanvasMode.POLY and len(self.poly_pts) > 2:
                self.finish_poly_shape()
        elif key == Qt.Key_Escape:
            self.cancel_drawing()
        elif key in [Qt.Key_Z, Qt.Key_X, Qt.Key_C, Qt.Key_V]:
            items = self.selectedItems()
            if items and isinstance(items[0], RotatedRectShape):
                delta = 0
                if key == Qt.Key_Z:
                    delta = -5
                elif key == Qt.Key_X:
                    delta = -1
                elif key == Qt.Key_C:
                    delta = 1
                elif key == Qt.Key_V:
                    delta = 5
                if delta != 0:
                    item = items[0]
                    item.setRotation(item.rotation() + delta)
                    self.state_changed.emit()

        super().keyPressEvent(event)
