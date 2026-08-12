"""Photo Studio - the image editing workspace.

Canvas with zoom/pan, crop, local adjustments (Pillow), inpaint-mask brush,
version history with A/B wipe compare, and the Nano Tools panel wired to any
image-edit model (Nano Banana by default).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QFileDialog, QGraphicsPixmapItem, QGraphicsScene,
                               QGraphicsView, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QScrollArea, QSlider,
                               QSplitter, QToolButton, QVBoxLayout, QWidget)

from ...core import media as media_utils
from ...core import paths
from .. import theme
from ..widgets.common import DropAcceptor, SliderSpin, accent_button, label
from .nano_tools import NanoToolsPanel


class PhotoCanvas(QGraphicsView):
    """Zoomable canvas with optional mask painting and crop selection."""
    maskPainted = Signal()
    cropSelected = Signal(QRectF)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene_ = QGraphicsScene()
        self.setScene(self.scene_)
        self.pix_item = QGraphicsPixmapItem()
        self.mask_item = QGraphicsPixmapItem()
        self.mask_item.setZValue(10)
        self.mask_item.setOpacity(0.45)
        self.scene_.addItem(self.pix_item)
        self.scene_.addItem(self.mask_item)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor("#141618"))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.mask_mode = False
        self.crop_mode = False
        self.brush_size = 48
        self.mask_image: QImage | None = None
        self._painting = False
        self._crop_start: QPointF | None = None
        self._crop_rect_item = None

    # ------------------------------------------------------------- display
    def set_pixmap(self, pm: QPixmap):
        self.pix_item.setPixmap(pm)
        self.scene_.setSceneRect(QRectF(pm.rect()))
        self.reset_mask()
        self.fit()

    def fit(self):
        if not self.pix_item.pixmap().isNull():
            self.fitInView(self.pix_item, Qt.AspectRatioMode.KeepAspectRatio)

    def zoom_100(self):
        self.resetTransform()

    def wheelEvent(self, ev):
        factor = 1.15 if ev.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    # --------------------------------------------------------------- mask
    def set_mask_mode(self, on: bool):
        self.mask_mode = on
        self.crop_mode = False if on else self.crop_mode
        self.setDragMode(QGraphicsView.DragMode.NoDrag if on or self.crop_mode
                         else QGraphicsView.DragMode.ScrollHandDrag)
        self.setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.ArrowCursor)

    def set_crop_mode(self, on: bool):
        self.crop_mode = on
        self.mask_mode = False if on else self.mask_mode
        self.setDragMode(QGraphicsView.DragMode.NoDrag if on or self.mask_mode
                         else QGraphicsView.DragMode.ScrollHandDrag)

    def reset_mask(self):
        pm = self.pix_item.pixmap()
        if pm.isNull():
            self.mask_image = None
            self.mask_item.setPixmap(QPixmap())
            return
        self.mask_image = QImage(pm.size(), QImage.Format.Format_ARGB32)
        self.mask_image.fill(Qt.GlobalColor.transparent)
        self._sync_mask()

    def _sync_mask(self):
        if self.mask_image is not None:
            self.mask_item.setPixmap(QPixmap.fromImage(self.mask_image))

    def has_mask(self) -> bool:
        if self.mask_image is None:
            return False
        img = self.mask_image.scaled(64, 64)
        for y in range(img.height()):
            for x in range(img.width()):
                if img.pixelColor(x, y).alpha() > 10:
                    return True
        return False

    def export_mask(self) -> Path | None:
        """White-on-black mask PNG (white = edit region) for inpainting APIs."""
        if self.mask_image is None or not self.has_mask():
            return None
        out = paths.cache_dir() / "mask.png"
        w, h = self.mask_image.width(), self.mask_image.height()
        bw = QImage(w, h, QImage.Format.Format_RGB32)
        bw.fill(QColor("black"))
        p = QPainter(bw)
        p.drawImage(0, 0, self.mask_image)
        p.end()
        # painted pixels are red-ish -> convert to pure white via PIL threshold
        bw.save(str(out))
        im = Image.open(out).convert("L").point(lambda v: 255 if v > 16 else 0)
        im.save(out)
        return out

    def _paint_at(self, scene_pos: QPointF, erase: bool = False):
        if self.mask_image is None:
            return
        p = QPainter(self.mask_image)
        if erase:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 60, 60, 255))
        r = self.brush_size / 2
        p.drawEllipse(scene_pos, r, r)
        p.end()
        self._sync_mask()

    # --------------------------------------------------------------- events
    def mousePressEvent(self, ev):
        sp = self.mapToScene(ev.position().toPoint())
        if self.mask_mode and ev.button() in (Qt.MouseButton.LeftButton,
                                              Qt.MouseButton.RightButton):
            self._painting = True
            self._paint_at(sp, erase=ev.button() == Qt.MouseButton.RightButton)
            ev.accept()
            return
        if self.crop_mode and ev.button() == Qt.MouseButton.LeftButton:
            self._crop_start = sp
            if self._crop_rect_item:
                self.scene_.removeItem(self._crop_rect_item)
            self._crop_rect_item = self.scene_.addRect(
                QRectF(sp, sp), QPen(QColor(theme.ACCENT), 2, Qt.PenStyle.DashLine))
            self._crop_rect_item.setZValue(20)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        sp = self.mapToScene(ev.position().toPoint())
        if self._painting:
            self._paint_at(sp, erase=bool(ev.buttons() & Qt.MouseButton.RightButton))
            ev.accept()
            return
        if self.crop_mode and self._crop_start is not None and self._crop_rect_item:
            self._crop_rect_item.setRect(QRectF(self._crop_start, sp).normalized())
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._painting:
            self._painting = False
            self.maskPainted.emit()
        if self.crop_mode and self._crop_start is not None and self._crop_rect_item:
            rect = self._crop_rect_item.rect().intersected(self.scene_.sceneRect())
            self._crop_start = None
            if rect.width() > 8 and rect.height() > 8:
                self.cropSelected.emit(rect)
        super().mouseReleaseEvent(ev)


class PhotoStudio(QWidget):
    sendToBin = Signal(str, dict)       # path, meta
    useAsReference = Signal(str)
    sendToChat = Signal(str)
    status = Signal(str)

    def __init__(self, registry, settings, jobs, get_adapter, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.settings = settings
        self.jobs = jobs
        self.get_adapter = get_adapter
        self.versions: list[Path] = []
        self.vindex = -1
        self._compare = False
        self.canvas = PhotoCanvas()
        self.canvas.cropSelected.connect(self._crop_to)
        DropAcceptor(self.canvas, ("image",), lambda p: self.open_path(p[0]),
                    on_rejected=lambda bad: self.status.emit(
                        f"Photo Studio only opens images - skipped {len(bad)} file(s)."))

        split = QSplitter(Qt.Orientation.Horizontal, self)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)

        # ---------------------------------------------------- left: tools
        left_host = QWidget()
        left = QVBoxLayout(left_host)
        left.setContentsMargins(6, 6, 6, 6)
        left.setSpacing(5)

        open_btn = QPushButton("📂 Open image…")
        open_btn.clicked.connect(self._open_dialog)
        left.addWidget(open_btn)
        save_btn = QPushButton("💾 Save as…")
        save_btn.clicked.connect(self._save_as)
        left.addWidget(save_btn)

        row = QHBoxLayout()
        self.undo_btn = QPushButton("↩ Undo")
        self.undo_btn.clicked.connect(self.undo)
        self.redo_btn = QPushButton("↪ Redo")
        self.redo_btn.clicked.connect(self.redo)
        row.addWidget(self.undo_btn)
        row.addWidget(self.redo_btn)
        left.addLayout(row)

        row2 = QHBoxLayout()
        fit = QPushButton("Fit")
        fit.clicked.connect(lambda: self.canvas.fit())
        z100 = QPushButton("100%")
        z100.clicked.connect(lambda: self.canvas.zoom_100())
        row2.addWidget(fit)
        row2.addWidget(z100)
        left.addLayout(row2)

        left.addWidget(label("— Transform —", dim=True))
        trow = QHBoxLayout()
        for text, fn in (("⟲ 90°", lambda: self._transform("rot_l")),
                         ("⟳ 90°", lambda: self._transform("rot_r")),
                         ("⇋ H", lambda: self._transform("flip_h")),
                         ("⥮ V", lambda: self._transform("flip_v"))):
            b = QToolButton(text=text)
            b.clicked.connect(fn)
            trow.addWidget(b)
        left.addLayout(trow)
        self.crop_btn = QPushButton("⬚ Crop (drag on canvas)")
        self.crop_btn.setCheckable(True)
        self.crop_btn.toggled.connect(lambda on: self.canvas.set_crop_mode(on))
        left.addWidget(self.crop_btn)

        left.addWidget(label("— Inpaint mask —", dim=True))
        self.mask_btn = QPushButton("🖌 Paint mask (R-click erases)")
        self.mask_btn.setCheckable(True)
        self.mask_btn.toggled.connect(lambda on: self.canvas.set_mask_mode(on))
        left.addWidget(self.mask_btn)
        brow = QHBoxLayout()
        brow.addWidget(QLabel("Brush"))
        self.brush = QSlider(Qt.Orientation.Horizontal)
        self.brush.setRange(6, 220)
        self.brush.setValue(48)
        self.brush.valueChanged.connect(lambda v: setattr(self.canvas, "brush_size", v))
        brow.addWidget(self.brush, 1)
        left.addLayout(brow)
        clear_mask = QPushButton("✕ Clear mask")
        clear_mask.clicked.connect(self.canvas.reset_mask)
        left.addWidget(clear_mask)

        left.addWidget(label("— Adjustments —", dim=True))
        self.adj: dict[str, SliderSpin] = {}
        for name, lo, hi in (("Brightness", -100, 100), ("Contrast", -100, 100),
                             ("Saturation", -100, 100), ("Temperature", -100, 100),
                             ("Sharpness", 0, 100), ("Blur", 0, 30)):
            left.addWidget(QLabel(name))
            s = SliderSpin(lo, hi, 0)
            self.adj[name.lower()] = s
            left.addWidget(s)
        apply_btn = accent_button("Apply adjustments")
        apply_btn.clicked.connect(self._apply_adjustments)
        left.addWidget(apply_btn)
        reset_btn = QPushButton("Reset sliders")
        reset_btn.clicked.connect(lambda: [s.set_value(0) for s in self.adj.values()])
        left.addWidget(reset_btn)
        left.addStretch(1)

        left_scroll = QScrollArea()
        left_scroll.setWidget(left_host)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFixedWidth(210)
        left_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        split.addWidget(left_scroll)

        # ---------------------------------------------------- centre: canvas
        centre = QWidget()
        cv = QVBoxLayout(centre)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(3)
        cv.addWidget(self.canvas, 1)

        crow = QHBoxLayout()
        self.compare_btn = QPushButton("◧ Compare with previous")
        self.compare_btn.setCheckable(True)
        self.compare_btn.toggled.connect(self._toggle_compare)
        self.compare_slider = QSlider(Qt.Orientation.Horizontal)
        self.compare_slider.setRange(0, 100)
        self.compare_slider.setValue(50)
        self.compare_slider.valueChanged.connect(lambda _v: self._update_display())
        self.compare_slider.setVisible(False)
        crow.addWidget(self.compare_btn)
        crow.addWidget(self.compare_slider, 1)
        cv.addLayout(crow)

        self.filmstrip = QListWidget()
        self.filmstrip.setFlow(QListWidget.Flow.LeftToRight)
        self.filmstrip.setFixedHeight(84)
        self.filmstrip.setIconSize(QPixmap(96, 64).size())
        self.filmstrip.itemClicked.connect(self._jump_version)
        cv.addWidget(self.filmstrip)

        arow = QHBoxLayout()
        to_bin = accent_button("📥 Save to Project Bin")
        to_bin.clicked.connect(self._to_bin)
        ref = QPushButton("🧬 Use as gen reference")
        ref.clicked.connect(lambda: self.current_path and self.useAsReference.emit(str(self.current_path)))
        chat = QPushButton("💬 Send to chat")
        chat.clicked.connect(lambda: self.current_path and self.sendToChat.emit(str(self.current_path)))
        arow.addWidget(to_bin)
        arow.addWidget(ref)
        arow.addWidget(chat)
        arow.addStretch(1)
        cv.addLayout(arow)
        split.addWidget(centre)

        # ---------------------------------------------------- right: AI tools
        self.nano = NanoToolsPanel(registry, settings)
        self.nano.runRequested.connect(self.run_ai_edit)
        nano_scroll = QScrollArea()
        nano_scroll.setWidget(self.nano)
        nano_scroll.setWidgetResizable(True)
        nano_scroll.setMinimumWidth(270)
        nano_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        split.addWidget(nano_scroll)
        split.setSizes([210, 900, 300])

    # ------------------------------------------------------------ versions
    @property
    def current_path(self) -> Path | None:
        return self.versions[self.vindex] if 0 <= self.vindex < len(self.versions) else None

    def open_path(self, path):
        p = Path(path)
        if not p.exists():
            return
        dest = paths.unique_path(paths.studio_dir(), p.stem, p.suffix or ".png")
        shutil.copy2(p, dest)
        self._push_version(dest)
        self.status.emit(f"Opened {p.name} in Photo Studio")

    def _open_dialog(self):
        f, _ = QFileDialog.getOpenFileName(self, "Open image", "", media_utils.IMAGE_FILTER)
        if f:
            self.open_path(f)

    def _push_version(self, path: Path):
        self.versions = self.versions[: self.vindex + 1]
        self.versions.append(Path(path))
        self.vindex = len(self.versions) - 1
        self._refresh_filmstrip()
        self._update_display()

    def _refresh_filmstrip(self):
        self.filmstrip.clear()
        from PySide6.QtGui import QIcon
        for i, p in enumerate(self.versions):
            it = QListWidgetItem(QIcon(QPixmap(str(p)).scaled(
                96, 64, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)), f"v{i + 1}")
            it.setData(Qt.ItemDataRole.UserRole, i)
            self.filmstrip.addItem(it)
        self.filmstrip.setCurrentRow(self.vindex)

    def _jump_version(self, item):
        self.vindex = item.data(Qt.ItemDataRole.UserRole)
        self._update_display()

    def undo(self):
        if self.vindex > 0:
            self.vindex -= 1
            self.filmstrip.setCurrentRow(self.vindex)
            self._update_display()

    def redo(self):
        if self.vindex < len(self.versions) - 1:
            self.vindex += 1
            self.filmstrip.setCurrentRow(self.vindex)
            self._update_display()

    def _update_display(self):
        p = self.current_path
        if not p:
            return
        pm = QPixmap(str(p))
        if self._compare and self.vindex > 0:
            prev = QPixmap(str(self.versions[self.vindex - 1])).scaled(
                pm.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            combo = QPixmap(pm.size())
            painter = QPainter(combo)
            painter.drawPixmap(0, 0, pm)
            split_x = int(pm.width() * self.compare_slider.value() / 100)
            painter.drawPixmap(0, 0, prev, 0, 0, split_x, prev.height())
            painter.setPen(QPen(QColor(theme.ORANGE), 3))
            painter.drawLine(split_x, 0, split_x, pm.height())
            painter.end()
            pm = combo
        self.canvas.set_pixmap(pm)

    def _toggle_compare(self, on: bool):
        self._compare = on
        self.compare_slider.setVisible(on)
        self._update_display()

    def refresh_theme(self):
        """Called by MainWindow after a theme/density switch. The compare
        divider line color is composited into the displayed pixmap (not
        redrawn live by Qt), so it needs an explicit recompose to pick up
        the new accent color."""
        if self.current_path:
            self._update_display()

    # ------------------------------------------------------------ local ops
    def _pil(self) -> Image.Image | None:
        return Image.open(self.current_path).convert("RGB") if self.current_path else None

    def _commit_pil(self, im: Image.Image, stem: str):
        out = paths.unique_path(paths.studio_dir(), stem, ".png")
        im.save(out)
        self._push_version(out)

    def _transform(self, op: str):
        im = self._pil()
        if not im:
            return
        if op == "rot_l":
            im = im.transpose(Image.Transpose.ROTATE_90)
        elif op == "rot_r":
            im = im.transpose(Image.Transpose.ROTATE_270)
        elif op == "flip_h":
            im = im.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        elif op == "flip_v":
            im = im.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        self._commit_pil(im, "transform")

    def _crop_to(self, rect: QRectF):
        im = self._pil()
        if not im:
            return
        box = (max(0, int(rect.left())), max(0, int(rect.top())),
               min(im.width, int(rect.right())), min(im.height, int(rect.bottom())))
        if box[2] - box[0] > 4 and box[3] - box[1] > 4:
            self._commit_pil(im.crop(box), "crop")
        self.crop_btn.setChecked(False)

    def _apply_adjustments(self):
        im = self._pil()
        if not im:
            return
        v = {k: s.value() for k, s in self.adj.items()}
        if v["brightness"]:
            im = ImageEnhance.Brightness(im).enhance(1 + v["brightness"] / 100)
        if v["contrast"]:
            im = ImageEnhance.Contrast(im).enhance(1 + v["contrast"] / 100)
        if v["saturation"]:
            im = ImageEnhance.Color(im).enhance(1 + v["saturation"] / 100)
        if v["temperature"]:
            r, g, b = im.split()
            t = v["temperature"] / 100
            r = r.point(lambda px: min(255, int(px * (1 + 0.3 * t))))
            b = b.point(lambda px: min(255, int(px * (1 - 0.3 * t))))
            im = Image.merge("RGB", (r, g, b))
        if v["sharpness"]:
            im = ImageEnhance.Sharpness(im).enhance(1 + v["sharpness"] / 50)
        if v["blur"]:
            im = im.filter(ImageFilter.GaussianBlur(v["blur"] / 4))
        self._commit_pil(im, "adjust")
        for s in self.adj.values():
            s.set_value(0)

    # ------------------------------------------------------------ AI edits
    def run_ai_edit(self, prompt: str, needs_mask: bool, tool_name: str):
        src = self.current_path
        if not src:
            self.status.emit("Open an image in Photo Studio first.")
            return
        model = self.nano.current_model()
        if model is None:
            self.status.emit("No image-edit model selected - check Model Manager / API keys.")
            return
        mask = self.canvas.export_mask()
        if needs_mask and mask is None:
            self.status.emit(f"“{tool_name}” needs a mask - paint one first (🖌).")
            return
        adapter = self.get_adapter(model.provider)
        params = model.default_params()

        def work(job):
            job.progress(-1, f"{tool_name} via {model.display}")
            return adapter.edit_image(model.id, prompt, str(src),
                                      str(mask) if mask else None, params)

        def done(result):
            for out in result or []:
                dest = paths.unique_path(paths.studio_dir(), tool_name.replace(" ", "_"), ".png")
                shutil.copy2(out, dest)
                self._push_version(dest)
            self.canvas.reset_mask()
            self.status.emit(f"✓ {tool_name} done ({model.display})")

        self.jobs.submit(f"{tool_name} · {model.display}", work, kind="image",
                         on_done=done,
                         on_fail=lambda msg: self.status.emit(f"✗ {tool_name}: {msg}"))

    # ------------------------------------------------------------ output
    def _save_as(self):
        if not self.current_path:
            return
        f, _ = QFileDialog.getSaveFileName(self, "Save image", "edited.png",
                                           "PNG (*.png);;JPEG (*.jpg)")
        if f:
            Image.open(self.current_path).save(f)
            self.status.emit(f"Saved {f}")

    def _to_bin(self):
        if self.current_path:
            self.sendToBin.emit(str(self.current_path), {"source": "photo_studio"})
            self.status.emit("Added to Project Bin")
