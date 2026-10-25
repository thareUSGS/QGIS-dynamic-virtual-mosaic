"""GDAL helpers for raster inspection and VRT source-path management."""

import math
import os
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlsplit, urlunsplit

from osgeo import gdal, osr


def _is_remote_source(source):
    """Recognize URLs and GDAL virtual filesystem source names."""
    return source.startswith("/vsi") or urlsplit(source).scheme.lower() in (
        "http",
        "https",
    )


def path_key(path):
    """Return a stable key for local paths, VSI paths, and remote URLs."""
    source = str(path)
    vsi_prefix = ""
    if source.startswith("/vsi"):
        vsi_position = source.find("/http")
        if vsi_position >= 0:
            vsi_prefix = source[: vsi_position + 1]
            source = source[vsi_position + 1 :]
        else:
            return source

    parsed = urlsplit(source)
    if parsed.scheme.lower() in ("http", "https"):
        normalized_url = urlunsplit(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path,
                parsed.query,
                parsed.fragment,
            )
        )
        return vsi_prefix + normalized_url

    if vsi_prefix:
        return vsi_prefix + source
    return os.path.normcase(os.path.abspath(source))


def normalize_source_path(path):
    """Preserve URLs and GDAL VSI paths; normalize only local filesystem paths."""
    source = str(path)
    if _is_remote_source(source):
        return source
    return os.path.abspath(source)


def source_name(path):
    """Return a readable raster filename from a path or remote source URI."""
    source = str(path)
    if source.startswith("/vsi"):
        vsi_position = source.find("/http")
        if vsi_position >= 0:
            source = source[vsi_position + 1 :]
    parsed = urlsplit(source)
    if parsed.scheme.lower() in ("http", "https"):
        path_part = unquote(parsed.path)
    else:
        path_part = source
    return os.path.basename(path_part.rstrip("/")) or source


def raster_info(path):
    """Read the raster properties shown in the table and used for footprints."""
    dataset = gdal.Open(path, gdal.GA_ReadOnly)
    if dataset is None:
        raise RuntimeError("Could not open raster: {}".format(path))

    transform = dataset.GetGeoTransform()
    width = dataset.RasterXSize
    height = dataset.RasterYSize
    corners = ((0, 0), (width, 0), (0, height), (width, height))
    coordinates = [
        (
            transform[0] + pixel * transform[1] + line * transform[2],
            transform[3] + pixel * transform[4] + line * transform[5],
        )
        for pixel, line in corners
    ]
    xs, ys = zip(*coordinates)

    # Pixel vectors account for rotated rasters as well as north-up images.
    info = {
        "resolution": "{:.6g} x {:.6g}".format(
            math.hypot(transform[1], transform[4]),
            math.hypot(transform[2], transform[5]),
        ),
        "bands": dataset.RasterCount,
        "extent": "{:.6f}, {:.6f}, {:.6f}, {:.6f}".format(
            min(xs), min(ys), max(xs), max(ys)
        ),
        "geotransform": transform,
        "width": width,
        "height": height,
    }

    # Release the GDAL handle promptly, which is especially important on Windows.
    dataset = None
    return info


def projection_differences(paths):
    """Return source names whose CRS differs from the first source, if any."""
    if len(paths) < 2:
        return []

    reference_projection = None
    different_sources = []
    for index, path in enumerate(paths):
        dataset = gdal.Open(path, gdal.GA_ReadOnly)
        if dataset is None:
            raise RuntimeError("Could not open raster projection: {}".format(path))

        projection_wkt = dataset.GetProjection()
        dataset = None
        projection = None
        if projection_wkt:
            projection = osr.SpatialReference()
            result = projection.ImportFromWkt(projection_wkt)
            if result != 0:
                raise RuntimeError(
                    "Could not read the projection for raster: {}".format(path)
                )

        if index == 0:
            reference_projection = projection
            continue

        # Treat a defined CRS and a missing CRS as different projection states.
        matches_reference = (
            reference_projection is None and projection is None
        ) or (
            reference_projection is not None
            and projection is not None
            and bool(reference_projection.IsSame(projection))
        )
        if not matches_reference:
            different_sources.append(source_name(path))

    return different_sources


def vrt_sources(path):
    """Return unique VRT source paths in the table's top-to-bottom order."""
    root = ET.parse(path).getroot()
    sources = []
    source_keys = set()

    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "SourceFilename" or not element.text:
            continue

        source = element.text.strip()
        if element.attrib.get("relativeToVRT") == "1" and not _is_remote_source(
            source
        ):
            source = os.path.join(os.path.dirname(path), source)
        source = normalize_source_path(source)

        key = path_key(source)
        if key not in source_keys:
            sources.append(source)
            source_keys.add(key)

    # VRT XML records draw order (bottom to top); the table is top to bottom.
    return list(reversed(sources))


def make_sources_relative(vrt_path, final_path):
    """Rewrite VRT source links relative to the final path whenever possible."""
    tree = ET.parse(vrt_path)
    output_directory = os.path.dirname(final_path)

    for element in tree.getroot().iter():
        if element.tag.rsplit("}", 1)[-1] != "SourceFilename" or not element.text:
            continue

        source = element.text.strip()
        if element.attrib.get("relativeToVRT") == "1" and not _is_remote_source(
            source
        ):
            source = os.path.normpath(
                os.path.join(os.path.dirname(vrt_path), source)
            )

        if _is_remote_source(source):
            element.text = source
            element.set("relativeToVRT", "0")
            continue

        try:
            # relpath raises ValueError for sources on another Windows drive.
            relative_path = os.path.relpath(source, output_directory)
            source_drive = os.path.splitdrive(source)[0].lower()
            output_drive = os.path.splitdrive(output_directory)[0].lower()
            if source_drive != output_drive:
                raise ValueError("Source is on another drive")
        except ValueError:
            element.text = os.path.abspath(source)
            element.set("relativeToVRT", "0")
        else:
            element.text = relative_path
            element.set("relativeToVRT", "1")

    tree.write(vrt_path, encoding="UTF-8", xml_declaration=True)


def build_vrt(path, sources, nodata=None, allow_projection_difference=False):
    """Build a VRT with explicit NoData and projection-mismatch options."""
    options = {"allowProjectionDifference": allow_projection_difference}
    if nodata is not None:
        options["VRTNodata"] = nodata

    build_options = gdal.BuildVRTOptions(**options)
    return gdal.BuildVRT(path, sources, options=build_options)
