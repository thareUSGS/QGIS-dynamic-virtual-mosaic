"""Build a versioned ZIP that can be installed as a QGIS plugin."""

import argparse
import configparser
import os
from pathlib import Path
import re
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIRECTORY = REPOSITORY_ROOT / "dynamic_virtual_mosaic"
VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?\Z")


def read_plugin_version():
    """Read and validate the release version from QGIS plugin metadata."""
    metadata = configparser.ConfigParser()
    metadata_path = PLUGIN_DIRECTORY / "metadata.txt"
    if not metadata.read(metadata_path, encoding="utf-8"):
        raise ValueError("Could not read plugin metadata: {}".format(metadata_path))

    version = metadata.get("general", "version", fallback="").strip()
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(
            "Plugin metadata version must use a numeric version such as 1.2.3."
        )
    return version


def package_plugin(project_name, version, output_directory):
    """Write the plugin's files beneath its required top-level ZIP folder."""
    archive_name = "{}-v{}.zip".format(project_name, version)
    archive_path = output_directory / archive_name
    output_directory.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(
        archive_path, mode="w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        plugin_files = sorted(
            path
            for path in PLUGIN_DIRECTORY.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix not in {".pyc", ".pyo"}
        )
        for file_path in plugin_files:
            archive_path_in_zip = Path(PLUGIN_DIRECTORY.name) / file_path.relative_to(
                PLUGIN_DIRECTORY
            )
            archive.write(file_path, archive_path_in_zip.as_posix())
        project_license = REPOSITORY_ROOT / "LICENSE"
        if project_license.is_file():
            archive.write(
                project_license,
                (Path(PLUGIN_DIRECTORY.name) / "LICENSE").as_posix(),
            )

    return archive_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="Release tag, for example v1.2.3")
    parser.add_argument(
        "--project-name",
        required=True,
        help="Project name used as the release archive name prefix",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=REPOSITORY_ROOT / "dist",
        help="Directory in which to write the ZIP archive",
    )
    arguments = parser.parse_args()

    version = read_plugin_version()
    expected_tag = "v{}".format(version)
    if arguments.tag != expected_tag:
        raise SystemExit(
            "Release tag {!r} does not match plugin metadata version {!r}.".format(
                arguments.tag, expected_tag
            )
        )

    archive_path = package_plugin(
        arguments.project_name, version, arguments.output_directory
    )
    print("archive={}".format(archive_path.resolve()))
    print("version={}".format(version))

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as output:
            output.write("archive={}\n".format(archive_path.resolve()))
            output.write("version={}\n".format(version))


if __name__ == "__main__":
    main()
