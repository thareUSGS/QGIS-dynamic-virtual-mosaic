# Dynamic Virtual Mosaic for QGIS

This QGIS plugin creates and edits ordered GDAL virtual raster mosaics (VRTs).
It requires QGIS 3.22 or newer and uses the GDAL bindings included with QGIS.

## Code layout

- `plugin.py` owns the QGIS action, pending edits, undo history, and Apply workflow.
- `dialog.py` builds the table and connects interface controls to plugin actions.
- `raster_utils.py` reads raster metadata and handles VRT source order and paths.
- `footprint_utils.py` builds the in-memory footprint layer and exports it.
- `metadata.txt` and `__init__.py` provide the QGIS plugin registration details.

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
   populate the table from an existing VRT.
3. Use **Move up**, **Move down**, and **Remove** to edit the pending image
   order. **Undo** restores the previous pending table and output selection;
   it does not roll back a VRT that was already applied to disk.
4. Choose **Apply** to write the VRT. On the first apply, choose an output
   `.vrt` path. Later applies update that same file. Source references are
   stored relative to the output directory whenever possible.
   Optionally enter a numeric **VRT NoData override**; leave it blank to use
   GDAL's normal source NoData behavior. The value applies to every VRT band.
5. Choose **Create footprints** to add an in-memory polygon layer containing
   one footprint per image. **Export footprints** writes that layer to a
   GeoPackage.

The VRT is created or changed only by **Apply**. The table's **Order** column
shows the last applied numeric source order and is refreshed after applying.
Footprints use each source raster's full pixel extent and the VRT's coordinate
reference system.

When source projections differ, the dialog lists the affected images and Apply
is blocked unless **Allow images with different projections** is selected.
GDAL's VRT builder can include those sources, but it does not reproject them;
the plugin shows a confirmation before building because the resulting mosaic
may be spatially misaligned. Reproject the source images to a common CRS first
when a correctly aligned mosaic is required.
