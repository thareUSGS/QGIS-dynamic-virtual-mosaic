"""Creation and export helpers for VRT source footprint layers."""

import os

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsCoordinateTransform,
    QgsCsException,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsVectorFileWriter,
    QgsVectorLayer,
)

from .raster_utils import raster_info


def _source_footprint(info):
    """Build a polygon from all four raster corners, including rotation."""
    transform = info["geotransform"]
    width = info["width"]
    height = info["height"]
    pixel_corners = (
        (0, 0),
        (width, 0),
        (width, height),
        (0, height),
        (0, 0),
    )
    map_corners = [
        QgsPointXY(
            transform[0] + pixel * transform[1] + line * transform[2],
            transform[3] + pixel * transform[4] + line * transform[5],
        )
        for pixel, line in pixel_corners
    ]
    return QgsGeometry.fromPolygonXY([map_corners])


def create_footprint_layer(source_paths, vrt_path):
    """Create an in-memory polygon layer in the loaded VRT's CRS."""
    target_layer = QgsRasterLayer(vrt_path, "Footprint CRS")
    if not target_layer.isValid():
        raise RuntimeError("The current VRT could not be opened.")

    target_crs = target_layer.crs()
    layer = QgsVectorLayer("Polygon", "VRT image footprints", "memory")
    if not layer.isValid():
        raise RuntimeError("Could not create the in-memory footprint layer.")
    layer.setCrs(target_crs)

    # Keep one stable attribute schema for in-memory and GeoPackage output.
    provider = layer.dataProvider()
    fields = [
        QgsField("image", QVariant.String),
        QgsField("resolution", QVariant.String),
        QgsField("bands", QVariant.Int),
        QgsField("order", QVariant.Int),
    ]
    if not provider.addAttributes(fields):
        raise RuntimeError("Could not create footprint attributes.")
    layer.updateFields()

    project = QgsProject.instance()
    for image_order, path in enumerate(source_paths, start=1):
        info = raster_info(path)
        source_layer = QgsRasterLayer(path, os.path.basename(path))
        if not source_layer.isValid():
            raise RuntimeError(
                "Could not open the footprint source raster: {}".format(path)
            )

        source_crs = source_layer.crs()
        if source_crs.isValid() != target_crs.isValid():
            raise RuntimeError(
                "The source and VRT coordinate systems are not compatible: {}".format(
                    path
                )
            )

        geometry = _source_footprint(info)
        if source_crs.isValid() and source_crs != target_crs:
            # Keep footprints consistent by expressing every geometry in VRT CRS.
            transform = QgsCoordinateTransform(source_crs, target_crs, project)
            try:
                geometry.transform(transform)
            except QgsCsException as error:
                raise RuntimeError(
                    "Could not transform footprint for {}: {}".format(path, error)
                )

        feature = QgsFeature(layer.fields())
        feature.setGeometry(geometry)
        feature.setAttributes(
            [
                os.path.basename(path),
                info["resolution"],
                info["bands"],
                image_order,
            ]
        )
        if not provider.addFeature(feature):
            raise RuntimeError("Could not add footprint for {}.".format(path))

    layer.updateExtents()
    return layer


def export_footprint_layer(layer, path):
    """Write an in-memory footprint layer to the selected GeoPackage path."""
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.fileEncoding = "UTF-8"
    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer,
        path,
        QgsProject.instance().transformContext(),
        options,
    )
    if result[0] != QgsVectorFileWriter.NoError:
        raise RuntimeError("Could not export footprints: {}".format(result[1]))
