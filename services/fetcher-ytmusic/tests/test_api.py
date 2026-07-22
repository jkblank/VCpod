from fetcher_ytmusic.api import _best_thumbnail_url


def test_best_thumbnail_url_upscales_the_largest_available():
    thumbnails = [
        {"url": "https://yt3.googleusercontent.com/abc=w60-h60-l90-rj", "width": 60, "height": 60},
        {"url": "https://yt3.googleusercontent.com/abc=w120-h120-l90-rj", "width": 120, "height": 120},
    ]

    result = _best_thumbnail_url(thumbnails)

    assert result == "https://yt3.googleusercontent.com/abc=w1200-h1200-l90-rj"


def test_best_thumbnail_url_picks_largest_regardless_of_list_order():
    thumbnails = [
        {"url": "https://yt3.googleusercontent.com/big=w120-h120-l90-rj", "width": 120, "height": 120},
        {"url": "https://yt3.googleusercontent.com/small=w60-h60-l90-rj", "width": 60, "height": 60},
    ]

    result = _best_thumbnail_url(thumbnails)

    assert result == "https://yt3.googleusercontent.com/big=w1200-h1200-l90-rj"


def test_best_thumbnail_url_returns_none_for_empty_or_missing_thumbnails():
    assert _best_thumbnail_url([]) is None
    assert _best_thumbnail_url(None) is None


def test_best_thumbnail_url_falls_back_to_original_url_if_pattern_not_found():
    thumbnails = [{"url": "https://example.com/cover.jpg", "width": 100, "height": 100}]

    result = _best_thumbnail_url(thumbnails)

    assert result == "https://example.com/cover.jpg"
