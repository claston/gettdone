from app.application.native_text_quality import assess_native_text_quality


def test_unmapped_font_glyphs_require_visual_extraction_even_without_a_raster():
    quality = assess_native_text_quality(b"not-needed-for-text-signal", ["□" * 80 + "123 A" * 40])
    assert quality.requires_visual_extraction
    assert "unmapped_font_glyphs" in quality.evidence


def test_valid_portuguese_and_a_few_missing_label_glyphs_keep_native_reading():
    quality = assess_native_text_quality(b"", ["Extrato da Conta Investimento ação 1.200,00 " * 30 + "□\x00"])
    assert not quality.requires_visual_extraction


def test_one_corrupted_page_cannot_be_hidden_by_other_readable_pages():
    quality = assess_native_text_quality(b"", ["Extrato legível 1.200,00 " * 500, "□" * 50 + "123 " * 20])
    assert quality.requires_visual_extraction
