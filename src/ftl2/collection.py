"""Lightweight Galaxy API client for installing Ansible collections.

Replaces `ansible-galaxy collection install` without requiring the full
ansible package. Uses httpx (already an ftl2 dependency) to download
collection tarballs from Galaxy and extract them.
"""

import io
import json
import tarfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from ftl2.module_loading.fqcn import DEFAULT_COLLECTION_PATHS

GALAXY_API = "https://galaxy.ansible.com/api/v3"


@dataclass
class CollectionInfo:
    namespace: str
    name: str
    version: str
    path: Path


def parse_collection_arg(arg: str) -> tuple[str, str, str | None]:
    """Parse 'namespace.collection' or 'namespace.collection:version'."""
    version = None
    if ":" in arg:
        arg, version = arg.rsplit(":", 1)
    parts = arg.split(".")
    if len(parts) != 2:
        raise ValueError(f"Invalid collection name: {arg} (expected namespace.collection)")
    return parts[0], parts[1], version


def get_default_path() -> Path:
    return DEFAULT_COLLECTION_PATHS[0]


def install_collection(
    name: str,
    version: str | None = None,
    path: Path | None = None,
    force: bool = False,
    galaxy_url: str = GALAXY_API,
) -> CollectionInfo:
    """Install a collection from Galaxy."""
    namespace, collection, parsed_version = parse_collection_arg(name)
    version = version or parsed_version

    install_path = path or get_default_path()
    dest = install_path / "ansible_collections" / namespace / collection

    if dest.exists() and not force:
        # Check installed version
        manifest = dest / "MANIFEST.json"
        if manifest.exists():
            info = json.loads(manifest.read_text())
            installed_ver = info.get("collection_info", {}).get("version", "unknown")
            if version and installed_ver == version:
                print(f"{namespace}.{collection} {installed_ver} already installed (use --force to reinstall)")
                return CollectionInfo(namespace, collection, installed_ver, dest)
            if not version:
                print(f"{namespace}.{collection} {installed_ver} already installed (use --force to reinstall)")
                return CollectionInfo(namespace, collection, installed_ver, dest)

    # Find version
    with httpx.Client(follow_redirects=True) as client:
        if version:
            url = f"{galaxy_url}/plugin/ansible/content/published/collections/index/{namespace}/{collection}/versions/{version}/"
            resp = client.get(url)
            if resp.status_code == 404:
                raise ValueError(f"Version {version} not found for {namespace}.{collection}")
            resp.raise_for_status()
            version_data = resp.json()
        else:
            url = f"{galaxy_url}/plugin/ansible/content/published/collections/index/{namespace}/{collection}/versions/?limit=1&ordering=-version"
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("data"):
                raise ValueError(f"Collection {namespace}.{collection} not found on Galaxy")
            version_data = data["data"][0]
            # Get full version detail for download_url
            resp = client.get(f"https://galaxy.ansible.com{version_data['href']}")
            resp.raise_for_status()
            version_data = resp.json()

        version = version_data["version"]
        download_url = version_data["download_url"]
        expected_sha256 = version_data.get("artifact", {}).get("sha256")

        print(f"Downloading {namespace}.{collection} {version}...")
        resp = client.get(download_url)
        resp.raise_for_status()
        tarball_bytes = resp.content

    # Verify SHA256 if available
    if expected_sha256:
        import hashlib
        actual_sha256 = hashlib.sha256(tarball_bytes).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError(
                f"SHA256 mismatch: expected {expected_sha256}, got {actual_sha256}"
            )

    # Extract tarball
    dest.mkdir(parents=True, exist_ok=True)
    prefix = f"{namespace}-{collection}-{version}/"

    with tarfile.open(fileobj=io.BytesIO(tarball_bytes), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.name.startswith(prefix):
                member.name = member.name[len(prefix):]
            if not member.name or member.name == ".":
                continue
            # Security: prevent path traversal
            if member.name.startswith("/") or ".." in member.name:
                continue
            tar.extract(member, dest, filter="data")

    print(f"Installed {namespace}.{collection} {version} to {dest}")
    return CollectionInfo(namespace, collection, version, dest)


def list_collections(path: Path | None = None) -> list[CollectionInfo]:
    """List installed collections."""
    search_paths = [path] if path else DEFAULT_COLLECTION_PATHS
    collections: list[CollectionInfo] = []

    for base in search_paths:
        ac_dir = base / "ansible_collections"
        if not ac_dir.exists():
            continue
        for ns_dir in sorted(ac_dir.iterdir()):
            if not ns_dir.is_dir() or ns_dir.name.startswith("."):
                continue
            for coll_dir in sorted(ns_dir.iterdir()):
                if not coll_dir.is_dir() or coll_dir.name.startswith("."):
                    continue
                version = "unknown"
                manifest = coll_dir / "MANIFEST.json"
                galaxy_yml = coll_dir / "galaxy.yml"
                if manifest.exists():
                    try:
                        info = json.loads(manifest.read_text())
                        version = info.get("collection_info", {}).get("version", "unknown")
                    except (json.JSONDecodeError, KeyError):
                        pass
                elif galaxy_yml.exists():
                    # Simple YAML parse for version line
                    for line in galaxy_yml.read_text().splitlines():
                        if line.startswith("version:"):
                            version = line.split(":", 1)[1].strip().strip("'\"")
                            break
                collections.append(CollectionInfo(ns_dir.name, coll_dir.name, version, coll_dir))

    return collections
