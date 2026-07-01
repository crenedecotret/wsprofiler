from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView

from ..ti.ti2 import Patch


@dataclass(slots=True)
class RenderPatch:
    patch: Patch
    rect: QRectF
    item: "QGraphicsRectItem"
    # For split display during measurement
    measured_color: tuple[int, int, int] | None = None
    top_item: "QGraphicsRectItem | None" = None  # Original color
    bottom_item: "QGraphicsRectItem | None" = None  # Measured color


class ChartScene(QGraphicsScene):
    # Emitted when user clicks on a strip during measurement mode
    stripClicked = Signal(int)  # strip number clicked
    
    def __init__(self) -> None:
        super().__init__()
        self._render_patches: list[RenderPatch] = []
        self._highlight: QGraphicsRectItem | None = None
        self._current_strip: int | None = None
        self._measurement_mode: bool = False
        self._strip_label_items: dict[int, QGraphicsSimpleTextItem] = {}
        self._strip_check_items: dict[int, QGraphicsSimpleTextItem] = {}
        self._measured_strips: set[int] = set()

    def load_patches(self, patches: Sequence[Patch], current_page: int = 1, strips_per_page: int = 26) -> None:
        self.clear()
        self._render_patches.clear()
        self._strip_label_items.clear()
        self._strip_check_items.clear()
        self._measured_strips.clear()
        if not patches:
            return

        patch_height = 22
        patch_width = 22
        horizontal_gap = 3
        vertical_gap = 3
        header_height = 20
        page_gap = 0

        # Argyll: strips run VERTICALLY (columns), patches stack vertically within strip
        # Sort by strip (column), then by position (row within column)
        sorted_patches = sorted(patches, key=lambda p: (p.strip, p.position))

        strip_spacing = patch_width + horizontal_gap
        patch_spacing = patch_height + vertical_gap

        # Group patches by strip
        strips = sorted(set(p.strip for p in sorted_patches))
        patches_by_strip = {s: [p for p in sorted_patches if p.strip == s] for s in strips}

        for strip in strips:
            # Calculate page and column within page
            page_index = (strip - 1) // strips_per_page
            col_in_page = (strip - 1) % strips_per_page

            # Position: pages side by side horizontally
            page_offset_x = page_index * (strips_per_page * strip_spacing + page_gap)
            x = page_offset_x + col_in_page * strip_spacing

            # Add strip header label (A-Z, AA-AZ), centered above column
            label = self._strip_to_label(strip)
            text = self.addSimpleText(label)
            text.setBrush(QColor("#333"))
            # Center text: x + half patch width - half text width
            text_x = x + (patch_width - text.boundingRect().width()) / 2
            text.setPos(text_x, 2)
            self._strip_label_items[strip] = text

            # Add hidden checkmark at bottom of strip (shown when measured)
            check = self.addSimpleText("")  # Empty initially, will be set to checkmark when measured
            check.setBrush(QColor("#1F8A4C"))
            check.setVisible(False)
            font = check.font()
            font.setBold(True)
            check.setFont(font)
            # Position at bottom of column (will update after patches are drawn)
            self._strip_check_items[strip] = check

            # Draw patches for this strip
            for patch in patches_by_strip[strip]:
                y = header_height + (patch.position - 1) * patch_spacing
                rect = QRectF(x, y, patch_width, patch_height)
                color = QColor(*patch.approx_rgb())
                item = self.addRect(rect, brush=color)
                
                # Create diagonal split items for measured display (hidden by default)
                # Top-left triangle: original color
                # Bottom-right triangle: measured color
                
                # Top-left triangle: (x,y), (x+width,y), (x,y+height)
                poly1 = QPolygonF([
                    QPointF(x, y),
                    QPointF(x + patch_width, y),
                    QPointF(x, y + patch_height)
                ])
                # Bottom-right triangle: (x+width,y), (x+width,y+height), (x,y+height)
                poly2 = QPolygonF([
                    QPointF(x + patch_width, y),
                    QPointF(x + patch_width, y + patch_height),
                    QPointF(x, y + patch_height)
                ])
                
                top_item = self.addPolygon(poly1, brush=color)  # Original/target
                bottom_item = self.addPolygon(poly2, brush=color)  # Measured
                top_item.setVisible(False)
                bottom_item.setVisible(False)
                
                self._render_patches.append(RenderPatch(
                    patch=patch, rect=rect, item=item,
                    top_item=top_item, bottom_item=bottom_item
                ))

            # Position checkmark at bottom of this strip's column
            check_item = self._strip_check_items.get(strip)
            if check_item:
                check_x = x + (patch_width - check_item.boundingRect().width()) / 2
                check_y = header_height + len(patches_by_strip[strip]) * patch_spacing + 2
                check_item.setPos(check_x, check_y)

        bounds = self.itemsBoundingRect().adjusted(-10, -10, 10, 10)
        self.setSceneRect(bounds)
        self._highlight = self.addRect(QRectF(), QPen(QColor("#FF6F61"), 3))
        self._highlight.setVisible(False)

    @staticmethod
    def _strip_to_label(strip: int) -> str:
        """Convert strip number to label (1=A, 2=B, ..., 27=AA, 28=AB, etc.)"""
        label = ""
        n = strip
        while n > 0:
            n -= 1
            label = chr(ord('A') + (n % 26)) + label
            n //= 26
        return label or "A"

    def highlight_strip(self, strip: int | None) -> None:
        self._current_strip = strip
        if not self._render_patches or not self._highlight:
            return
        if strip is None:
            self._highlight.setVisible(False)
            return
        rows = [rp.rect for rp in self._render_patches if rp.patch.strip == strip]
        if not rows:
            self._highlight.setVisible(False)
            return
        bounds = QRectF(rows[0])
        for rect in rows[1:]:
            bounds = bounds.united(rect)
        self._highlight.setRect(bounds.adjusted(-4, -4, 4, 4))
        self._highlight.setVisible(True)

    def mousePressEvent(self, event) -> None:
        """Handle mouse clicks to navigate strips during measurement."""
        if not self._measurement_mode or not self._render_patches:
            super().mousePressEvent(event)
            return

        # Match by column (x-coordinate) so clicks in the gaps between patches,
        # on the header label, or on the checkmark area all still register as
        # a strip click. Pick the strip whose column center is closest to x.
        pos = event.scenePos()
        x = pos.x()
        best_strip: int | None = None
        best_dx: float = float("inf")
        for rp in self._render_patches:
            center_x = rp.rect.x() + rp.rect.width() / 2
            dx = abs(x - center_x)
            if dx < best_dx:
                best_dx = dx
                best_strip = rp.patch.strip
        # Require the click to be within roughly one strip-spacing of a column
        # so clicks far outside the chart don't trigger spurious navigation.
        if best_strip is not None and best_dx < 40:
            if best_strip != self._current_strip:
                self.stripClicked.emit(best_strip)
            return

        super().mousePressEvent(event)

    def set_measurement_mode(self, enabled: bool) -> None:
        """Enable/disable split-patch display mode."""
        self._measurement_mode = enabled
        for rp in self._render_patches:
            if enabled and rp.top_item and rp.bottom_item:
                # Show split view
                rp.item.setVisible(False)
                rp.top_item.setVisible(True)
                rp.bottom_item.setVisible(True)
                # Update colors
                orig_color = QColor(*rp.patch.approx_rgb())
                rp.top_item.setBrush(orig_color)
                if rp.measured_color:
                    meas_color = QColor(*rp.measured_color)
                    rp.bottom_item.setBrush(meas_color)
                else:
                    # Mirror the target color until this patch has been measured
                    # so unread patches look like solid swatches.
                    rp.bottom_item.setBrush(orig_color)
            else:
                # Show single color view
                rp.item.setVisible(True)
                if rp.top_item:
                    rp.top_item.setVisible(False)
                if rp.bottom_item:
                    rp.bottom_item.setVisible(False)

    def update_measured_colors(self, measured_patches: dict[str, tuple[int, int, int]]) -> None:
        """Update measured colors for patches.
        
        measured_patches: dict mapping SAMPLE_LOC to (R, G, B)
        """
        for rp in self._render_patches:
            loc = rp.patch.sample_loc
            if loc in measured_patches:
                rp.measured_color = measured_patches[loc]
                if rp.bottom_item and rp.bottom_item.isVisible():
                    rp.bottom_item.setBrush(QColor(*rp.measured_color))

    def mark_strip_measured(self, strip: int) -> None:
        """Visually flag a strip as having been read in this session."""
        if strip in self._measured_strips:
            return
        self._measured_strips.add(strip)
        # Show checkmark at bottom of strip column
        check_item = self._strip_check_items.get(strip)
        if check_item is not None:
            check_item.setText("\u2713")  # ✓ checkmark
            check_item.setVisible(True)
        # Also color the header label green
        label_item = self._strip_label_items.get(strip)
        if label_item is not None:
            label_item.setBrush(QColor("#1F8A4C"))
            font = label_item.font()
            font.setBold(True)
            label_item.setFont(font)

    def reset_measured_marks(self) -> None:
        """Clear the measured-strip indicators (e.g. on session start)."""
        for strip in list(self._measured_strips):
            check_item = self._strip_check_items.get(strip)
            if check_item is not None:
                check_item.setText("")
                check_item.setVisible(False)
            label_item = self._strip_label_items.get(strip)
            if label_item is not None:
                label_item.setBrush(QColor("#333"))
                font = label_item.font()
                font.setBold(False)
                label_item.setFont(font)
        self._measured_strips.clear()

    def measured_strips(self) -> list[int]:
        """Return the list of strips that have been marked as measured."""
        return sorted(self._measured_strips)


class ChartView(QGraphicsView):
    # Forward the scene's stripClicked signal
    stripClicked = Signal(int)
    
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        # Horizontal scroll for multiple pages, vertical fits to viewport
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        scene = ChartScene()
        self.setScene(scene)
        # Connect scene signal to our signal
        scene.stripClicked.connect(self.stripClicked.emit)

    def set_patches(self, patches: Sequence[Patch], strips_per_page: int = 26) -> None:
        scene: ChartScene = self.scene()  # type: ignore[assignment]
        scene.load_patches(patches, strips_per_page=strips_per_page)
        self._fit_vertical()

    def _fit_vertical(self) -> None:
        """Scale to fit vertically, keep horizontal scrollable."""
        scene: ChartScene = self.scene()  # type: ignore[assignment]
        self.resetTransform()
        bounds = scene.sceneRect()
        if not bounds.isNull():
            view_size = self.viewport().size()
            if view_size.height() > 0:
                scale_y = view_size.height() / bounds.height()
                self.scale(scale_y, scale_y)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_vertical()

    def highlight_strip(self, strip: int | None) -> None:
        scene: ChartScene = self.scene()  # type: ignore[assignment]
        scene.highlight_strip(strip)
        # Center on highlighted strip
        if strip is not None:
            items = [rp.top_item or rp.item for rp in scene._render_patches if rp.patch.strip == strip]
            if items:
                bounds = items[0].sceneBoundingRect()
                for item in items[1:]:
                    bounds = bounds.united(item.sceneBoundingRect())
                center = bounds.center()
                self.centerOn(center)

    def set_measurement_mode(self, enabled: bool) -> None:
        """Enable/disable split-patch display showing measured colors."""
        scene: ChartScene = self.scene()  # type: ignore[assignment]
        scene.set_measurement_mode(enabled)

    def update_measured_colors(self, measured_patches: dict[str, tuple[int, int, int]]) -> None:
        """Update measured colors from TI3 file.
        
        measured_patches: dict mapping SAMPLE_LOC to (R, G, B) measured color
        """
        scene: ChartScene = self.scene()  # type: ignore[assignment]
        scene.update_measured_colors(measured_patches)

    def mark_strip_measured(self, strip: int) -> None:
        scene: ChartScene = self.scene()  # type: ignore[assignment]
        scene.mark_strip_measured(strip)

    def measured_strips(self) -> list[int]:
        """Return the list of strips that have been marked as measured."""
        scene: ChartScene = self.scene()  # type: ignore[assignment]
        return scene.measured_strips()

    def reset_measured_marks(self) -> None:
        scene: ChartScene = self.scene()  # type: ignore[assignment]
        scene.reset_measured_marks()
