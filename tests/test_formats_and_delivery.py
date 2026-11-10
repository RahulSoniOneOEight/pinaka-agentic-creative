from __future__ import annotations

import json

import pytest

from pinaka.delivery import build_delivery_packet
from pinaka.formats import FORMAT_SPECS, format_output_name
from pinaka.utils import sha256_file


def test_format_specs_cover_delivery_formats():
    assert [spec.key for spec in FORMAT_SPECS] == ["9x16", "1x1", "16x9", "4x5"]
    assert FORMAT_SPECS[0].width_px == 1080
    assert FORMAT_SPECS[0].height_px == 1920
    assert FORMAT_SPECS[1].width_px == FORMAT_SPECS[1].height_px == 1080
    assert FORMAT_SPECS[2].width_px == 1920
    assert FORMAT_SPECS[2].height_px == 1080
    assert FORMAT_SPECS[3].height_px == 1350


def test_format_output_name_is_stable():
    assert (
        format_output_name("animatic-voiced-fce922", "16x9")
        == "animatic-voiced-fce922-16x9.mp4"
    )


def test_delivery_packet_copies_inputs_and_hashes(tmp_path):
    master = tmp_path / "master.mp4"
    captioned = tmp_path / "captioned.mp4"
    captions = tmp_path / "captions.srt"
    formats_manifest = tmp_path / "formats-manifest.json"
    metadata = tmp_path / "metadata.json"
    master.write_bytes(b"master-video")
    captioned.write_bytes(b"captioned-video")
    captions.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")
    formats_manifest.write_text(json.dumps({"formats": []}))
    metadata.write_text(json.dumps({"title": "Pilot"}))
    formats_dir = tmp_path / "formats"
    formats_dir.mkdir()
    (formats_dir / "video-16x9.mp4").write_bytes(b"format-video")

    output = tmp_path / "packet"
    manifest = build_delivery_packet(
        master=master,
        captioned=captioned,
        captions=captions,
        formats_manifest=formats_manifest,
        metadata=metadata,
        output_dir=output,
        formats_dir=formats_dir,
    )

    assert manifest["publishing_authorized"] is False
    assert len(manifest["items"]) == 6
    assert (output / "delivery-manifest.json").is_file()
    assert (output / "formats" / "video-16x9.mp4").is_file()
    for item in manifest["items"]:
        target = output / item["name"]
        assert target.is_file()
        assert sha256_file(target) == item["sha256"]


def test_delivery_packet_refuses_existing_directory(tmp_path):
    master = tmp_path / "master.mp4"
    captioned = tmp_path / "captioned.mp4"
    captions = tmp_path / "captions.srt"
    formats_manifest = tmp_path / "formats-manifest.json"
    metadata = tmp_path / "metadata.json"
    for path in (master, captioned, captions, formats_manifest, metadata):
        path.write_bytes(b"x")
    output = tmp_path / "packet"
    output.mkdir()
    with pytest.raises(FileExistsError):
        build_delivery_packet(
            master=master,
            captioned=captioned,
            captions=captions,
            formats_manifest=formats_manifest,
            metadata=metadata,
            output_dir=output,
        )
