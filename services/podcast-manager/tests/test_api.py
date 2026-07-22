from podcast_manager import api as api_module
from podcast_manager.api import PodcastSummary, resolve_show_selection


class FakeResponse:
    def __init__(self, data: dict):
        self._data = data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._data


def test_login_returns_token(monkeypatch):
    def fake_post(url, json, timeout=None):
        assert url == api_module.LOGIN_URL
        assert json == {"email": "a@b.com", "password": "pw", "scope": "webplayer"}
        return FakeResponse({"token": "tok123"})

    monkeypatch.setattr(api_module.httpx, "post", fake_post)

    assert api_module.login("a@b.com", "pw") == "tok123"


def test_list_subscriptions_parses_podcasts(monkeypatch):
    def fake_post(url, headers, json, timeout=None):
        assert headers["Authorization"] == "Bearer tok"
        return FakeResponse(
            {
                "podcasts": [
                    {"uuid": "p1", "title": "Show One", "author": "Author One"},
                    {"uuid": "p2", "title": "Show Two", "author": "Author Two"},
                ]
            }
        )

    monkeypatch.setattr(api_module.httpx, "post", fake_post)

    result = api_module.list_subscriptions("tok")

    assert [p.uuid for p in result] == ["p1", "p2"]
    assert result[0].title == "Show One"
    assert result[0].author == "Author One"


def test_list_episode_states_parses_camel_case_fields(monkeypatch):
    def fake_post(url, headers, json, timeout=None):
        assert url == api_module.PODCAST_EPISODES_URL
        return FakeResponse(
            {
                "episodes": [
                    {"uuid": "e1", "playingStatus": 3, "playedUpTo": 100},
                    {"uuid": "e2", "playingStatus": 2, "playedUpTo": 0},
                ]
            }
        )

    monkeypatch.setattr(api_module.httpx, "post", fake_post)

    result = api_module.list_episode_states("tok", "podcast-1")

    assert len(result) == 2
    assert result[0].played is True
    assert result[0].played_up_to == 100
    assert result[1].played is False


def test_list_episode_states_parses_snake_case_fields(monkeypatch):
    def fake_post(url, headers, json, timeout=None):
        return FakeResponse({"episodes": [{"uuid": "e1", "playing_status": 3, "played_up_to": 50}]})

    monkeypatch.setattr(api_module.httpx, "post", fake_post)

    result = api_module.list_episode_states("tok", "podcast-1")

    assert result[0].played is True
    assert result[0].played_up_to == 50


def test_list_episode_states_empty_for_untouched_podcast(monkeypatch):
    # Confirmed against a real account: Pocket Casts returns no rows at all
    # for episodes still in their default (unplayed) state.
    monkeypatch.setattr(
        api_module.httpx,
        "post",
        lambda url, headers, json, timeout=None: FakeResponse({"episodes": []}),
    )

    assert api_module.list_episode_states("tok", "podcast-1") == []


def test_list_full_episodes_parses_catalog_with_audio_urls(monkeypatch):
    def fake_get(url, headers, follow_redirects, timeout=None):
        assert url == api_module.PODCAST_FULL_URL.format(uuid="podcast-1")
        return FakeResponse(
            {
                "podcast": {
                    "episodes": [
                        {
                            "uuid": "e1",
                            "title": "Episode One",
                            "url": "https://cdn.example/e1.mp3",
                            "published": "2026-01-01T00:00:00Z",
                            "duration": 100,
                        },
                        {"uuid": "e2", "title": "No Audio Episode"},  # no url — skipped
                    ]
                }
            }
        )

    monkeypatch.setattr(api_module.httpx, "get", fake_get)

    result = api_module.list_full_episodes("tok", "podcast-1")

    assert len(result) == 1
    assert result[0].uuid == "e1"
    assert result[0].url == "https://cdn.example/e1.mp3"
    assert result[0].duration == 100


# --- resolve_show_selection --------------------------------------------------

SUBSCRIPTIONS = [
    PodcastSummary(uuid="uuid-1", title="Waveform", author="The Vergecast Network"),
    PodcastSummary(uuid="uuid-2", title="Reply All", author="Gimlet"),
]


def test_resolve_show_selection_matches_by_uuid():
    matched, unmatched = resolve_show_selection(SUBSCRIPTIONS, ["uuid-2"])

    assert [p.title for p in matched] == ["Reply All"]
    assert unmatched == []


def test_resolve_show_selection_matches_by_title_case_insensitively():
    matched, unmatched = resolve_show_selection(SUBSCRIPTIONS, ["waveform"])

    assert [p.uuid for p in matched] == ["uuid-1"]
    assert unmatched == []


def test_resolve_show_selection_reports_unmatched_entries():
    matched, unmatched = resolve_show_selection(SUBSCRIPTIONS, ["Reply All", "Nonexistent Show"])

    assert [p.uuid for p in matched] == ["uuid-2"]
    assert unmatched == ["Nonexistent Show"]


def test_resolve_show_selection_dedups_when_same_show_requested_twice():
    matched, unmatched = resolve_show_selection(SUBSCRIPTIONS, ["uuid-1", "waveform"])

    assert [p.uuid for p in matched] == ["uuid-1"]
    assert unmatched == []
