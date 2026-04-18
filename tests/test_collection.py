"""Tests for ftl2.collection module."""

import json

import pytest

from ftl2.collection import (
    list_collections,
    parse_collection_arg,
)


class TestParseCollectionArg:
    def test_simple(self):
        ns, name, version = parse_collection_arg("ansible.utils")
        assert ns == "ansible"
        assert name == "utils"
        assert version is None

    def test_with_version(self):
        ns, name, version = parse_collection_arg("ansible.utils:2.0.0")
        assert ns == "ansible"
        assert name == "utils"
        assert version == "2.0.0"

    def test_invalid_single_part(self):
        with pytest.raises(ValueError, match="Invalid collection name"):
            parse_collection_arg("ansible")

    def test_invalid_three_parts(self):
        with pytest.raises(ValueError, match="Invalid collection name"):
            parse_collection_arg("a.b.c")

    def test_version_with_colon_in_version(self):
        # rsplit on ":" so "ns.coll:1.0.0" works even with dots
        ns, name, version = parse_collection_arg("ns.coll:1.0.0")
        assert version == "1.0.0"


class TestListCollections:
    def test_empty_path(self, tmp_path):
        result = list_collections(path=tmp_path)
        assert result == []

    def test_with_manifest(self, tmp_path):
        coll_dir = tmp_path / "ansible_collections" / "ansible" / "utils"
        coll_dir.mkdir(parents=True)
        manifest = coll_dir / "MANIFEST.json"
        manifest.write_text(json.dumps({
            "collection_info": {"version": "3.1.0"}
        }))

        result = list_collections(path=tmp_path)
        assert len(result) == 1
        assert result[0].namespace == "ansible"
        assert result[0].name == "utils"
        assert result[0].version == "3.1.0"

    def test_with_galaxy_yml(self, tmp_path):
        coll_dir = tmp_path / "ansible_collections" / "community" / "general"
        coll_dir.mkdir(parents=True)
        galaxy = coll_dir / "galaxy.yml"
        galaxy.write_text("namespace: community\nname: general\nversion: 8.0.0\n")

        result = list_collections(path=tmp_path)
        assert len(result) == 1
        assert result[0].version == "8.0.0"

    def test_no_version_info(self, tmp_path):
        coll_dir = tmp_path / "ansible_collections" / "custom" / "stuff"
        coll_dir.mkdir(parents=True)

        result = list_collections(path=tmp_path)
        assert len(result) == 1
        assert result[0].version == "unknown"

    def test_multiple_collections(self, tmp_path):
        for ns, name in [("ansible", "utils"), ("ansible", "netcommon"), ("community", "general")]:
            d = tmp_path / "ansible_collections" / ns / name
            d.mkdir(parents=True)

        result = list_collections(path=tmp_path)
        assert len(result) == 3

    def test_skips_hidden_dirs(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        ac.mkdir()
        (ac / ".git").mkdir()
        (ac / "ansible").mkdir()
        (ac / "ansible" / ".cache").mkdir()
        real = ac / "ansible" / "utils"
        real.mkdir()

        result = list_collections(path=tmp_path)
        assert len(result) == 1
        assert result[0].name == "utils"

    def test_bad_manifest_json(self, tmp_path):
        coll_dir = tmp_path / "ansible_collections" / "ns" / "coll"
        coll_dir.mkdir(parents=True)
        (coll_dir / "MANIFEST.json").write_text("not json")

        result = list_collections(path=tmp_path)
        assert len(result) == 1
        assert result[0].version == "unknown"

    def test_galaxy_yml_quoted_version(self, tmp_path):
        coll_dir = tmp_path / "ansible_collections" / "ns" / "coll"
        coll_dir.mkdir(parents=True)
        (coll_dir / "galaxy.yml").write_text("version: '1.2.3'\n")

        result = list_collections(path=tmp_path)
        assert result[0].version == "1.2.3"
