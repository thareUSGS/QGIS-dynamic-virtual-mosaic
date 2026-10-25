"""Creation and export helpers for VRT source footprint layers."""

import os
import tempfile

import numpy
from osgeo import gdal, ogr, osr
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

from .raster_utils import raster_info, source_name


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


def _valid_pixel_footprint(path):
    """Polygonize the shared valid-data mask for a raster's non-alpha bands."""
    source_dataset = gdal.Open(path, gdal.GA_ReadOnly)
    if source_dataset is None:
        raise RuntimeError("Could not open footprint source raster: {}".format(path))

    width = source_dataset.RasterXSize
    height = source_dataset.RasterYSize
    data_bands = []
    alpha_bands = []
    for band_index in range(1, source_dataset.RasterCount + 1):
        band = source_dataset.GetRasterBand(band_index)
        if band.GetColorInterpretation() == gdal.GCI_AlphaBand:
            alpha_bands.append(band)
        else:
            data_bands.append(band)
    if not data_bands:
        data_bands = alpha_bands
        alpha_bands = []

    descriptor, mask_path = tempfile.mkstemp(suffix=".tif")
    os.close(descriptor)
    mask_dataset = None
    memory_source = None
    try:
        driver = gdal.GetDriverByName("GTiff")
        mask_dataset = driver.Create(
            mask_path,
            width,
            height,
            1,
            gdal.GDT_Byte,
            options=["TILED=YES", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER"],
        )
        if mask_dataset is None:
            raise RuntimeError("Could not create temporary validity mask.")
        mask_dataset.SetGeoTransform(source_dataset.GetGeoTransform())
        projection_wkt = source_dataset.GetProjection()
        if projection_wkt:
            mask_dataset.SetProjection(projection_wkt)

        mask_band = mask_dataset.GetRasterBand(1)
        block_size = 512
        for y_offset in range(0, height, block_size):
            block_height = min(block_size, height - y_offset)
            for x_offset in range(0, width, block_size):
                block_width = min(block_size, width - x_offset)
                valid_pixels = numpy.ones(
                    (block_height, block_width), dtype=numpy.bool_
                )
                for band in data_bands:
                    band_mask = band.GetMaskBand().ReadAsArray(
                        x_offset, y_offset, block_width, block_height
                    )
                    if band_mask is None:
                        raise RuntimeError(
                            "Could not read validity mask for raster: {}".format(path)
                        )
                    valid_pixels &= band_mask != 0
                for band in alpha_bands:
                    alpha_values = band.ReadAsArray(
                        x_offset, y_offset, block_width, block_height
                    )
                    if alpha_values is None:
                        raise RuntimeError(
                            "Could not read alpha mask for raster: {}".format(path)
                        )
                    valid_pixels &= alpha_values != 0

                write_result = mask_band.WriteArray(
                    valid_pixels.astype(numpy.uint8),
                    x_offset,
                    y_offset,
                )
                if write_result != gdal.CE_None:
                    raise RuntimeError(
                        "Could not write validity mask for raster: {}".format(path)
                    )
        mask_band.FlushCache()

        spatial_reference = None
        if projection_wkt:
            spatial_reference = osr.SpatialReference()
            if spatial_reference.ImportFromWkt(projection_wkt) != 0:
                raise RuntimeError(
                    "Could not read the projection for raster: {}".format(path)
                )

        memory_driver = ogr.GetDriverByName("MEM")
        if memory_driver is None:
            memory_driver = ogr.GetDriverByName("Memory")
        if memory_driver is None:
            raise RuntimeError("GDAL does not provide an in-memory vector driver.")
        memory_source = memory_driver.CreateDataSource("valid_footprint")
        if memory_source is None:
            raise RuntimeError("Could not create temporary footprint polygons.")
        polygon_layer = memory_source.CreateLayer(
            "valid_pixels",
            srs=spatial_reference,
            geom_type=ogr.wkbPolygon,
        )
        if polygon_layer is None:
            raise RuntimeError("Could not create temporary footprint polygons.")
        if polygon_layer.CreateField(
            ogr.FieldDefn("valid", ogr.OFTInteger)
        ) != ogr.OGRERR_NONE:
            raise RuntimeError("Could not create temporary footprint attributes.")
        polygonize_result = gdal.Polygonize(
            mask_band,
            mask_band,
            polygon_layer,
            0,
            [],
        )
        if polygonize_result != gdal.CE_None:
            raise RuntimeError("Could not polygonize valid pixels: {}".format(path))

        geometries = []
        for feature in polygon_layer:
            geometry = feature.GetGeometryRef()
            if geometry and not geometry.IsEmpty():
                qgis_geometry = QgsGeometry()
                qgis_geometry.fromWkb(bytes(geometry.ExportToWkb()))
                if qgis_geometry.isNull():
                    raise RuntimeError(
                        "Could not read polygonized footprint geometry: {}".format(
                            path
                        )
                    )
                geometries.append(qgis_geometry)
        if not geometries:
            raise RuntimeError(
                "The raster contains no valid pixels for a footprint: {}".format(path)
            )

        footprint = QgsGeometry.unaryUnion(geometries)
        if footprint.isNull() or footprint.isEmpty():
            raise RuntimeError("Could not create valid-pixel footprint: {}".format(path))
        return footprint
    finally:
        geometry = None
        feature = None
        polygon_layer = None
        memory_source = None
        band = None
        data_bands = None
        alpha_bands = None
        mask_band = None
        mask_dataset = None
        source_dataset = None
        if os.path.exists(mask_path):
            os.remove(mask_path)


def create_footprint_layer(source_paths, vrt_path, valid_pixels=False):
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
        source_layer = QgsRasterLayer(path, source_name(path))
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

        geometry = (
            _valid_pixel_footprint(path)
            if valid_pixels
            else _source_footprint(info)
        )
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
                source_name(path),
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
