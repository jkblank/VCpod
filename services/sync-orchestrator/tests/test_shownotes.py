from sync_orchestrator.shownotes import html_to_plaintext


def test_plain_text_passes_through_unchanged():
    assert html_to_plaintext("A plain description.") == "A plain description."


def test_empty_description_stays_empty():
    assert html_to_plaintext("") == ""


def test_tags_are_removed_and_entities_decoded():
    assert html_to_plaintext("<b>Bold</b> &amp; <i>italic</i> &mdash; done") == "Bold & italic — done"


def test_paragraphs_become_line_breaks():
    html = "<p>First para.</p><p>Second para.</p>"

    assert html_to_plaintext(html) == "First para.\n\nSecond para."


def test_list_items_become_dashed_lines():
    html = "<ul><li>Chapter one</li><li>Chapter two</li></ul>"

    assert html_to_plaintext(html) == "- Chapter one\n- Chapter two"


def test_script_and_style_content_is_dropped():
    html = "<style>p{color:red}</style><p>Kept</p><script>alert(1)</script>"

    assert html_to_plaintext(html) == "Kept"


def test_whitespace_inside_lines_is_collapsed():
    assert html_to_plaintext("<p>Lots   of\n   spaces</p>") == "Lots of spaces"
