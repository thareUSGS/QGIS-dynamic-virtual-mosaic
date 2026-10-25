"""QGIS user interface and workflow for ordered GDAL VRT mosaics."""

import math
import os
import tempfile
import xml.etree.ElementTree as ET

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAction,
    QFileDialog,
    QInputDialog,
    QMessageBox,
    QTableWidgetItem,
)
from qgis.core import (
    QgsLayerTreeGroup,
    QgsLayerTreeLayer,
    QgsProject,
    QgsRasterLayer,
)

from .dialog import DynamicVirtualMosaicDialog, NumericTableWidgetItem
from .footprint_utils import create_footprint_layer, export_footprint_layer
from .raster_utils import (
    build_vrt,
    make_sources_relative,
    normalize_source_path,
    path_key,
    projection_differences,
    raster_info,
    source_name,
    vrt_sources,
)


class DynamicVirtualMosaicPlugin:
    """Manage a pending source list and apply it to a VRT on user request."""

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.widget = None
        self.table = None
        # `images` is the editable list; `applied_order` is the last saved VRT.
        self.images = []
        self.order_values = {}
        self.output_path = None
        self.applied_order = []
        # Undo snapshots restore the in-memory list, not a previously saved VRT.
        self.undo_stack = []
        self.mosaic_layer = None
        self.mosaic_layer_id = None
        self.displayed_path = None
        self.footprints_layer = None
        self.footprints_layer_id = None

    # Plugin lifecycle

    def initGui(self):
        """Register the plugin action in QGIS menus and the toolbar."""
        self.action = QAction("Dynamic Virtual Mosaic", self.iface.mainWindow())
        self.action.triggered.connect(self.show)
        self.iface.addPluginToMenu("&Dynamic Virtual Mosaic", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        """Remove plugin UI registrations when QGIS unloads the plugin."""
        if self.action:
            self.iface.removePluginMenu("&Dynamic Virtual Mosaic", self.action)
            self.iface.removeToolBarIcon(self.action)
        if self.widget:
            self.widget.close()

    def show(self):
        """Create the interface on first use, then show the existing window."""
        if self.widget is None:
            self.widget = DynamicVirtualMosaicDialog(
                self.iface.mainWindow(), self
            )
            self.table = self.widget.table
        self.widget.show()
        self.widget.raise_()
        self.widget.activateWindow()

    def _error(self, message):
        """Report an actionable operation failure to the user."""
        QMessageBox.critical(self.widget, "Dynamic Virtual Mosaic", message)

    def _push_undo(self):
        """Save the current table and applied-order state before a user edit."""
        self.undo_stack.append(
            (
                list(self.images),
                self.output_path,
                list(self.applied_order),
                dict(self.order_values),
            )
        )

    def _refresh_table(self):
        """Render the source list and retain each image's editable order value."""
        table = self.table
        header = table.horizontalHeader()
        sort_column = header.sortIndicatorSection()
        sort_order = header.sortIndicatorOrder()
        signals_blocked = table.blockSignals(True)
        table.setSortingEnabled(False)
        self.table.setRowCount(len(self.images))
        for row, path in enumerate(self.images):
            source_key = path_key(path)
            self.order_values.setdefault(source_key, float(row + 1))
            info = raster_info(path)
            values = (
                format(self.order_values[source_key], ".15g"),
                source_name(path),
                info["resolution"],
                str(info["bands"]),
                info["extent"],
            )
            for column, value in enumerate(values):
                item = (
                    NumericTableWidgetItem(value)
                    if column == 0
                    else QTableWidgetItem(value)
                )
                if column == 0:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                else:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 1:
                    item.setToolTip(path)
                    item.setData(Qt.ItemDataRole.UserRole, path)
                elif column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, path)
                self.table.setItem(row, column, item)
        table.setSortingEnabled(True)
        if sort_column >= 0:
            table.sortItems(sort_column, sort_order)
        table.blockSignals(signals_blocked)
        self.table.resizeColumnsToContents()
        self._refresh_projection_status()

    def _sync_images_from_table(self, update_order_values=True):
        """Use the currently displayed row order as the pending VRT order."""
        ordered_images = []
        for row in range(self.table.rowCount()):
            source_item = self.table.item(row, 1)
            order_item = self.table.item(row, 0)
            if source_item is None:
                continue
            source = source_item.data(Qt.ItemDataRole.UserRole)
            if not source:
                continue
            ordered_images.append(source)
            if order_item is not None and update_order_values:
                self.order_values[path_key(source)] = float(order_item.text())
        if len(ordered_images) == len(self.images):
            self.images = ordered_images

    def on_table_item_changed(self, item):
        """Validate editable numeric order values and retain them for sorting."""
        if item.column() != 0:
            return

        source = item.data(Qt.ItemDataRole.UserRole)
        if not source:
            return
        source_key = path_key(source)
        try:
            value = float(item.text())
            if not math.isfinite(value):
                raise ValueError
        except ValueError:
            previous_value = self.order_values.get(source_key)
            self.table.blockSignals(True)
            item.setText(
                format(previous_value, ".15g") if previous_value is not None else ""
            )
            self.table.blockSignals(False)
            QMessageBox.warning(
                self.widget,
                "Invalid order value",
                "Enter a finite numeric value in the Order column.",
            )
            return

        self._push_undo()
        self._sync_images_from_table(update_order_values=False)
        self.order_values[source_key] = value

    def _refresh_projection_status(self):
        """Explain mixed source CRSs and the effect of the GDAL opt-in."""
        if not self.images:
            self.widget.projection_status.setText("")
            return
        try:
            different_sources = projection_differences(self.images)
        except (OSError, RuntimeError, ValueError) as error:
            self.widget.projection_status.setText(
                "Could not inspect source projections: {}".format(error)
            )
            return

        if not different_sources:
            self.widget.projection_status.setText(
                "All source images share the same projection."
            )
            return

        names = ", ".join(different_sources)
        if self.widget.allow_projection_difference.isChecked():
            self.widget.projection_status.setText(
                "Projection mismatch detected ({}). GDAL will include these "
                "images without reprojecting them; the mosaic may be misaligned.".format(
                    names
                )
            )
        else:
            self.widget.projection_status.setText(
                "Projection mismatch detected ({}). Enable the projection option "
                "to include these images; GDAL will not reproject them.".format(
                    names
                )
            )

    def on_projection_option_changed(self, _checked):
        """Refresh the warning immediately when the projection option changes."""
        if self.widget:
            self._refresh_projection_status()

    # Source-list operations

    def add_images(self):
        """Append selected rasters without changing the displayed/applied VRT."""
        paths, _ = QFileDialog.getOpenFileNames(
            self.widget,
            "Add raster images",
            "",
            "Raster images (*.tif *.tiff *.img *.jp2 *.vrt);;All files (*.*)",
        )
        if not paths:
            return
        self._add_sources(paths)

    def add_from_group(self):
        """Append GDAL-backed raster layers from a selected project layer group."""
        groups = {}

        def collect_groups(group, group_path):
            group_sources = []
            for node in group.findLayers():
                layer = node.layer()
                if (
                    isinstance(node, QgsLayerTreeLayer)
                    and isinstance(layer, QgsRasterLayer)
                    and layer.isValid()
                    and layer.providerType() == "gdal"
                ):
                    group_sources.append(layer.source())

            if group_sources:
                groups[group_path] = group_sources

            for child in group.children():
                if isinstance(child, QgsLayerTreeGroup):
                    child_path = "{}/{}".format(group_path, child.name())
                    collect_groups(child, child_path)

        root = QgsProject.instance().layerTreeRoot()
        for child in root.children():
            if isinstance(child, QgsLayerTreeGroup):
                collect_groups(child, child.name())

        if not groups:
            self._error(
                "No groups with valid GDAL raster layers were found in the project."
            )
            return

        group_names = list(groups)
        selected_group, accepted = QInputDialog.getItem(
            self.widget,
            "Add images from group",
            "Choose a group of GDAL raster layers:",
            group_names,
            0,
            False,
        )
        if accepted and selected_group:
            self._add_sources(groups[selected_group])

    def _add_sources(self, sources):
        """Validate and append local paths or remote URIs to the pending list."""
        self._sync_images_from_table()
        existing_paths = {path_key(path) for path in self.images}
        accepted_sources = []
        errors = []

        for source in sources:
            normalized_source = normalize_source_path(source)
            source_key = path_key(normalized_source)
            if source_key in existing_paths:
                continue

            try:
                raster_info(normalized_source)
            except (RuntimeError, OSError, ValueError) as error:
                errors.append(
                    "{}: {}".format(source_name(normalized_source), error)
                )
                continue

            accepted_sources.append(normalized_source)
            existing_paths.add(source_key)

        if accepted_sources:
            self._push_undo()
            first_order = len(self.images) + 1
            self.images.extend(accepted_sources)
            for offset, source in enumerate(accepted_sources):
                self.order_values[path_key(source)] = float(first_order + offset)
            self._refresh_table()
        elif not errors:
            self._error("The selected source list contains no new raster images.")
            return

        if errors:
            QMessageBox.warning(
                self.widget,
                "Some images could not be added",
                "Skipped raster sources that GDAL could not open:\n\n{}".format(
                    "\n".join(errors)
                ),
            )

    def load_vrt(self):
        """Load an existing VRT, populate its source list, and display that VRT."""
        path, _ = QFileDialog.getOpenFileName(
            self.widget, "Load VRT", "", "Virtual rasters (*.vrt)"
        )
        if not path:
            return
        try:
            sources = vrt_sources(path)
            if not sources:
                sources = [os.path.abspath(path)]
            for source in sources:
                raster_info(source)
        except (OSError, RuntimeError, ValueError, ET.ParseError) as error:
            self._error("Could not load VRT: {}".format(error))
            return

        self._sync_images_from_table()
        self._push_undo()
        self.images = sources
        self.order_values = {
            path_key(source): float(index + 1)
            for index, source in enumerate(sources)
        }
        self.output_path = os.path.abspath(path)
        self.applied_order = list(sources)
        self._replace_mosaic_layer(self.output_path)
        self._refresh_table()

    def remove_selected(self):
        """Remove the selected source from the pending list."""
        self._sync_images_from_table()
        row = self.table.currentRow()
        if row < 0 or row >= len(self.images):
            return
        self._push_undo()
        removed_source = self.images.pop(row)
        self.order_values.pop(path_key(removed_source), None)
        self._refresh_table()

    def move_selected(self, direction):
        """Move the selected source one row without applying the change."""
        self._sync_images_from_table()
        row = self.table.currentRow()
        destination = row + direction
        if row < 0 or destination < 0 or destination >= len(self.images):
            return
        self._push_undo()
        self.table.setSortingEnabled(False)
        self.table.horizontalHeader().setSortIndicator(
            -1, Qt.SortOrder.AscendingOrder
        )
        self.images[row], self.images[destination] = (
            self.images[destination],
            self.images[row],
        )
        self._refresh_table()
        self.table.selectRow(destination)

    def undo(self):
        """Restore the previous table, output path, and applied-order snapshot."""
        if not self.undo_stack:
            return
        (
            self.images,
            self.output_path,
            self.applied_order,
            self.order_values,
        ) = self.undo_stack.pop()
        self._replace_mosaic_layer(
            self.output_path
            if self.output_path and os.path.isfile(self.output_path)
            else None
        )
        self._refresh_table()

    # VRT creation and map-layer refresh

    def apply(self):
        """Build a replacement VRT, then refresh its QGIS layer after success."""
        self._sync_images_from_table()
        if not self.images:
            self._error("Add at least one image before applying the mosaic.")
            return

        nodata_text = self.widget.nodata_input.text().strip()
        try:
            nodata = float(nodata_text) if nodata_text else None
        except ValueError:
            self._error("The VRT NoData override must be a numeric value.")
            return
        if nodata is not None and nodata in (float("inf"), float("-inf")):
            self._error("The VRT NoData override must be finite or NaN.")
            return

        try:
            different_sources = projection_differences(self.images)
        except (OSError, RuntimeError, ValueError) as error:
            self._error("Could not inspect source projections: {}".format(error))
            return

        allow_projection_difference = (
            self.widget.allow_projection_difference.isChecked()
        )
        if different_sources and not allow_projection_difference:
            names = ", ".join(different_sources)
            self._error(
                "Source images use different projections ({}). Enable the "
                "projection option to continue. GDAL will not reproject them.".format(
                    names
                )
            )
            return
        if different_sources:
            response = QMessageBox.warning(
                self.widget,
                "Different source projections",
                "GDAL will include the images with different projections, but "
                "will not reproject them. The mosaic may be spatially misaligned. "
                "Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if response != QMessageBox.StandardButton.Yes:
                return

        if not self.output_path:
            path, _ = QFileDialog.getSaveFileName(
                self.widget, "Save VRT", "", "Virtual rasters (*.vrt)"
            )
            if not path:
                return
            if not path.lower().endswith(".vrt"):
                path += ".vrt"
            self.output_path = os.path.abspath(path)

        if path_key(self.output_path) in {
            path_key(path) for path in self.images
        }:
            path, _ = QFileDialog.getSaveFileName(
                self.widget, "Save VRT as", "", "Virtual rasters (*.vrt)"
            )
            if not path:
                return
            if not path.lower().endswith(".vrt"):
                path += ".vrt"
            self.output_path = os.path.abspath(path)

        if path_key(self.output_path) in {
            path_key(path) for path in self.images
        }:
            self._error("Choose an output VRT that is not one of the source images.")
            return

        output_directory = os.path.dirname(self.output_path)
        if not os.path.isdir(output_directory):
            self._error("The output directory does not exist.")
            return

        temporary_path = None
        restore_layer_path = None
        try:
            descriptor, temporary_path = tempfile.mkstemp(
                suffix=".vrt", dir=output_directory, prefix=".dynamic-mosaic-"
            )
            os.close(descriptor)
            os.remove(temporary_path)
            # GDAL gives later sources precedence; reverse the table so row 1 is top.
            result = build_vrt(
                temporary_path,
                list(reversed(self.images)),
                nodata=nodata,
                allow_projection_difference=allow_projection_difference,
            )
            if result is None:
                raise RuntimeError("GDAL could not build a VRT from the selected images.")
            result.FlushCache()
            result = None
            # GDAL can skip invalid or incompatible inputs without failing the build.
            # Verify the output still includes every image selected by the user.
            built_sources = set(vrt_sources(temporary_path))
            built_keys = {path_key(path) for path in built_sources}
            missing_sources = [
                path for path in self.images if path_key(path) not in built_keys
            ]
            if missing_sources:
                names = ", ".join(source_name(path) for path in missing_sources)
                raise RuntimeError(
                    "GDAL did not include these source images: {}".format(names)
                )
            make_sources_relative(temporary_path, self.output_path)

            # Release the VRT before replacing it; GDAL may keep it open on Windows.
            if (
                self.mosaic_layer_id
                and self.displayed_path == path_key(self.output_path)
            ):
                restore_layer_path = self.output_path
                self._replace_mosaic_layer(None)
            # Build beside the destination so replacement is atomic on the same drive.
            os.replace(temporary_path, self.output_path)
            temporary_path = None
        except (RuntimeError, OSError, ValueError, ET.ParseError) as error:
            if restore_layer_path:
                self._replace_mosaic_layer(restore_layer_path)
            self._error("Could not apply the mosaic: {}".format(error))
            return
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.remove(temporary_path)

        self.applied_order = list(self.images)
        self._refresh_table()
        self._replace_mosaic_layer(self.output_path, force=True)

    def _replace_mosaic_layer(self, path, force=False):
        """Reload the VRT as a top-level layer, above groups and other layers."""
        project = QgsProject.instance()
        normalized_path = path_key(path) if path else None
        current_layer = (
            project.mapLayer(self.mosaic_layer_id)
            if self.mosaic_layer_id
            else None
        )
        if (
            not force
            and current_layer
            and current_layer.isValid()
            and self.displayed_path == normalized_path
        ):
            return
        if self.mosaic_layer_id:
            project.removeMapLayer(self.mosaic_layer_id)
        self.mosaic_layer = None
        self.mosaic_layer_id = None
        self.displayed_path = None
        if path is None:
            return
        layer = QgsRasterLayer(path, os.path.splitext(source_name(path))[0])
        if not layer.isValid():
            self._error("The VRT was saved, but QGIS could not load it as a raster layer.")
            return
        project.addMapLayer(layer, False)
        project.layerTreeRoot().insertLayer(0, layer)
        self.mosaic_layer = layer
        self.mosaic_layer_id = layer.id()
        self.displayed_path = normalized_path

    # In-memory footprints and GeoPackage export

    def create_footprints(self):
        """Create one transformed polygon per source in the currently applied VRT."""
        if not self.output_path or not os.path.isfile(self.output_path):
            self._error("Apply or load a VRT before creating footprints.")
            return
        try:
            layer = create_footprint_layer(
                self.applied_order,
                self.output_path,
                valid_pixels=self.widget.valid_pixel_footprints.isChecked(),
            )
        except (RuntimeError, OSError, ValueError) as error:
            self._error("Could not create footprints: {}".format(error))
            return

        project = QgsProject.instance()
        if self.footprints_layer_id:
            project.removeMapLayer(self.footprints_layer_id)
        layer.setOpacity(0.5)
        project.addMapLayer(layer, False)
        project.layerTreeRoot().insertLayer(0, layer)
        self.footprints_layer = layer
        self.footprints_layer_id = layer.id()

    def export_footprints(self):
        """Write the current in-memory footprint layer to a GeoPackage."""
        layer = (
            QgsProject.instance().mapLayer(self.footprints_layer_id)
            if self.footprints_layer_id
            else None
        )
        if not layer or not layer.isValid():
            self._error("Create the footprint layer before exporting it.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self.widget, "Export footprints", "", "GeoPackage (*.gpkg)"
        )
        if not path:
            return
        if not path.lower().endswith(".gpkg"):
            path += ".gpkg"
        try:
            export_footprint_layer(layer, path)
        except RuntimeError as error:
            self._error(str(error))
