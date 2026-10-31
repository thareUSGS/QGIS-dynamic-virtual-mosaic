# Dynamic Virtual Mosaic for QGIS

This QGIS plugin creates and edits ordered GDAL virtual raster mosaics (VRTs).
It supports QGIS 3.22 through 4.x and uses the GDAL bindings included with
QGIS.

## Code layout

- `plugin.py` owns the QGIS action, pending edits, undo history, and Apply workflow.
- `dialog.py` builds the table and connects interface controls to plugin actions.
- `raster_utils.py` reads raster metadata and handles VRT source order and paths.
- `footprint_utils.py` builds the in-memory footprint layer and exports it.
- `metadata.txt` and `__init__.py` provide the QGIS plugin registration details.

## License

This project is dedicated to the public domain under
[CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/).
See [LICENSE](./LICENSE) for the full legal text.

## Install

Copy the `dynamic_virtual_mosaic` folder into the QGIS user profile's Python
plugins directory, then enable **Dynamic Virtual Mosaic** from
**Plugins > Manage and Install Plugins > Installed**.

## Build a release

The GitHub Actions workflow builds a QGIS-installable ZIP and publishes a
GitHub Release when a version tag is pushed. The tag must match the version in
`dynamic_virtual_mosaic/metadata.txt`, with a `v` prefix. For example, for
version `0.1.0`, push the tag with:

```powershell
git tag v0.1.0
git push origin v0.1.0
```

The release includes `QGIS-dynamic-virtual-mosaic-0.1.0.zip`. The ZIP contains
the `dynamic_virtual_mosaic` plugin folder at its root, ready for installation
through QGIS's Plugin Manager.

## Use

1. Open **Dynamic Virtual Mosaic** from the Plugins menu or toolbar.
2. Choose **Add images** to build a VRT from raster files, or **Load VRT** to
   populate the table from an existing VRT. The table shows each image's
   horizontal (**Resolution X**) and vertical (**Resolution Y**) pixel sizes
   separately.
3. Use **Add from group** to import GDAL raster layers from a QGIS layer-tree
   group, including remote GeoTIFF/COG layers loaded from HTTPS URLs. Use
   **Move up**, **Move down**, and **Remove** to edit the pending image
   order. **Undo** restores the previous pending table and output selection;
   it does not roll back a VRT that was already applied to disk.
   Click a column heading to sort the table. **Resolution X**, **Resolution
   Y**, and **Extent Area** are numeric columns; the editable numeric **Order**
   values and **Save VRT** use the currently displayed row order. Extent Area
   is calculated from each image's displayed bounding extent and appears
   directly after Resolution Y. When all source images use projected meters,
   the resolution headers show `(m)` and Extent Area is converted to and
   labeled `(km^2)`. Otherwise, values remain in their native coordinate units
   and headers omit unit labels, avoiding misleading units for mixed CRSs.
4. Choose **Save VRT** to write the VRT. If no VRT is currently being edited,
   choose an output `.vrt` path; later saves rebuild and overwrite that file,
   then reload the VRT in QGIS at the top of the layer list, outside any
   selected group. Source references are stored relative to the output
   directory whenever possible. Choose **New VRT** to clear the current
   editing session, remove its displayed VRT layer from the project, and start
   an empty source list; this does not delete saved VRT files. Optionally enter
   a numeric **VRT NoData override**; leave it blank to use GDAL's normal
   source NoData behavior. The value applies to every VRT band.
5. Choose **Create footprints** to add an in-memory polygon layer containing
   one footprint per image, with the full source address in the `path` field
   and separate `resolution_X` and `resolution_Y` attributes. Local file paths
   are retained; HTTPS paths are stored without any leading GDAL VSI prefix.
   **Export footprints** writes that layer to a GeoPackage. By default,
   footprints use each image's outer rectangular extent. Enable **Follow valid
   pixels (exclude NoData)** first to polygonize raster validity masks instead;
   pixels must be valid in every non-alpha band. This detailed mode scans the
   raster and can take longer, especially for large or remote sources. The
   footprint layer is inserted at the top of the layer list with 50% opacity.

The VRT is created or changed only by **Save VRT**. Each save refreshes the
displayed raster layer so source-order changes are visible. The table's
**Order** column is an editable numeric sort key.

Footprints are expressed in the VRT's coordinate reference system.

When source projections differ, the dialog lists the affected images and Apply
is blocked unless **Allow images with different projections** is selected.
GDAL's VRT builder can include those sources, but it does not reproject them;
the plugin shows a confirmation before building because the resulting mosaic
may be spatially misaligned. Reproject the source images to a common CRS first
when a correctly aligned mosaic is required.
