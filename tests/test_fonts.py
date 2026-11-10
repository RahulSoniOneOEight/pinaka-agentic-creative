from __future__ import annotations

from pinaka.fonts import FontLicence, check_fonts, inventory_fonts


def make_font(tmp_path, name="Brand Sans.ttf", payload=b"not-a-real-font"):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_inventory_hashes_font_files(tmp_path):
    make_font(tmp_path)
    (tmp_path / "readme.txt").write_text("ignore me", encoding="utf-8")
    inventory = inventory_fonts(tmp_path)
    assert len(inventory.fonts) == 1
    assert inventory.fonts[0].relative_path == "Brand Sans.ttf"
    assert len(inventory.fonts[0].sha256) == 64
    assert inventory.manifest_hash


def test_unlicensed_font_is_blocked(tmp_path):
    make_font(tmp_path)
    inventory = inventory_fonts(tmp_path)
    report = check_fonts(inventory, [])
    assert report.counts["blocked"] == 1
    assert report.fonts[0].status == "blocked"


def test_licensed_font_can_be_eligible(tmp_path):
    make_font(tmp_path)
    inventory = inventory_fonts(tmp_path)
    licence = FontLicence(
        font_sha256=inventory.fonts[0].sha256,
        license_name="SIL Open Font License 1.1",
        license_url="https://openfontlicense.org/",
        embedding_allowed=True,
        redistribution_allowed=True,
        attribution_required=False,
        evidence_ref="workspace/licenses/brand-sans.txt",
    )
    report = check_fonts(inventory, [licence])
    assert report.fonts[0].status == "eligible"
    assert report.counts["eligible"] == 1


def test_wrong_hash_does_not_license_a_font(tmp_path):
    make_font(tmp_path)
    inventory = inventory_fonts(tmp_path)
    licence = FontLicence(
        font_sha256="f" * 64,
        license_name="SIL Open Font License 1.1",
        license_url="https://openfontlicense.org/",
        embedding_allowed=True,
        redistribution_allowed=True,
        evidence_ref="workspace/licenses/other-font.txt",
    )
    report = check_fonts(inventory, [licence])
    assert report.fonts[0].status == "blocked"


def test_embedding_prohibition_blocks_and_missing_evidence_reviews(tmp_path):
    make_font(tmp_path)
    inventory = inventory_fonts(tmp_path)
    fingerprint = inventory.fonts[0].sha256
    blocked = check_fonts(
        inventory,
        [
            FontLicence(
                font_sha256=fingerprint,
                license_name="Restricted",
                license_url="https://example.com/licence",
                embedding_allowed=False,
                redistribution_allowed=False,
                evidence_ref="workspace/licenses/restricted.txt",
            )
        ],
    )
    assert blocked.fonts[0].status == "blocked"
    review = check_fonts(
        inventory,
        [
            FontLicence(
                font_sha256=fingerprint,
                license_name="SIL Open Font License 1.1",
                license_url="https://openfontlicense.org/",
                embedding_allowed=True,
                redistribution_allowed=True,
            )
        ],
    )
    assert review.fonts[0].status == "needs_review"
