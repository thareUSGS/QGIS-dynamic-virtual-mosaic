"""Widget layout for the Dynamic Virtual Mosaic plugin."""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class NumericTableWidgetItem(QTableWidgetItem):
    """Sort numeric values instead of comparing their text lexicographically."""

    def __lt__(self, other):
        try:
            return float(self.text().split()[0]) < float(other.text().split()[0])
        except (IndexError, TypeError, ValueError):
            return super().__lt__(other)


class DynamicVirtualMosaicDialog(QDialog):
    """Present the mosaic table and connect controls to the plugin controller."""

    HEADERS = (
        "Order",
        "Image",
        "Resolution X",
        "Resolution Y",
        "Extent Area",
        "Bands",
        "Extent",
    )

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.setWindowTitle("Dynamic Virtual Mosaic")

        layout = QVBoxLayout(self)
        self.nodata_input = QLineEdit(self)
        self.nodata_input.setPlaceholderText("Leave blank to use source NoData")
        self.nodata_input.setToolTip(
            "Optional numeric NoData value to write to every VRT band."
        )
        layout.addWidget(QLabel("VRT NoData override (optional):", self))
        layout.addWidget(self.nodata_input)

        self.allow_projection_difference = QCheckBox(
            "Allow images with different projections (GDAL will not reproject)",
            self,
        )
        self.allow_projection_difference.setToolTip(
            "GDAL can include sources with differing projections, but it does not "
            "reproject them. Source coordinates may not align in the mosaic."
        )
        self.allow_projection_difference.toggled.connect(
            controller.on_projection_option_changed
        )
        layout.addWidget(self.allow_projection_difference)

        self.projection_status = QLabel(self)
        self.projection_status.setWordWrap(True)
        layout.addWidget(self.projection_status)

        self.valid_pixel_footprints = QCheckBox(
            "For footprints, follow valid pixels (exclude NoData; may take a long time)",
            self,
        )
        self.valid_pixel_footprints.setToolTip(
            "Creates a more detailed footprint by scanning raster validity masks. "
            "A pixel is included only when all non-alpha bands are valid. "
            "This may take a long time for large or remote rasters."
        )
        layout.addWidget(self.valid_pixel_footprints)

        self.table = self._create_table()
        self.table.itemChanged.connect(controller.on_table_item_changed)
        layout.addWidget(self.table)

        # Keep related actions in separate rows so the table remains easy to scan.
        file_actions = QHBoxLayout()
        self._add_button(file_actions, "Add images", controller.add_images)
        self._add_button(file_actions, "Add from group", controller.add_from_group)
        self._add_button(file_actions, "Load VRT", controller.load_vrt)
        self._add_button(file_actions, "Remove", controller.remove_selected)
        layout.addLayout(file_actions)

        order_actions = QHBoxLayout()
        self._add_button(
            order_actions, "Move up", lambda: controller.move_selected(-1)
        )
        self._add_button(
            order_actions, "Move to top", controller.move_selected_to_top
        )
        self._add_button(
            order_actions, "Move down", lambda: controller.move_selected(1)
        )
        self._add_button(order_actions, "Undo", controller.undo)
        layout.addLayout(order_actions)

        mosaic_actions = QHBoxLayout()
        self._add_button(mosaic_actions, "New VRT", controller.new_vrt)
        self._add_button(mosaic_actions, "Save VRT", controller.save_vrt)
        self._add_button(
            mosaic_actions, "Create footprints", controller.create_footprints
        )
        self._add_button(
            mosaic_actions, "Export footprints", controller.export_footprints
        )
        layout.addLayout(mosaic_actions)

        self.resize(1020, 520)

    def _create_table(self):
        """Create a sortable table with an editable numeric order column."""
        table = QTableWidget(0, len(self.HEADERS), self)
        table.setHorizontalHeaderLabels(self.HEADERS)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        header = table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        table.horizontalHeaderItem(0).setToolTip(
            "Editable numeric priority. Click a column heading to sort; "
            "Save VRT uses the displayed row order."
        )
        table.setSortingEnabled(True)
        return table

    def set_metric_units(self, metric):
        """Label the resolution and area columns when all sources use meters."""
        headers = list(self.HEADERS)
        if metric:
            headers[2] = "Resolution X (m)"
            headers[3] = "Resolution Y (m)"
            headers[4] = "Extent Area (km^2)"
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeaderItem(0).setToolTip(
            "Editable numeric priority. Click a column heading to sort; "
            "Save VRT uses the displayed row order."
        )

    def _add_button(self, layout, label, callback):
        """Add a parented control that invokes its matching controller action."""
        button = QPushButton(label, self)
        button.clicked.connect(callback)
        layout.addWidget(button)
