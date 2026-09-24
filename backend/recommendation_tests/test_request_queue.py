"""Server-side paging of the Requests queue: same rules the browser used to apply."""

import request_queue as rq

TODAY = "2026-09-24"


def _row(i, **extra):
    return {"id": f"r{i}", "status": "pending_approval", "title": f"Title {i:03d}", "year": 2020,
            "type": "movie", "updated_at": f"2026-09-{(i % 28) + 1:02d}T00:00:{i % 60:02d}", **extra}


def test_pages_cover_the_whole_filter_without_gaps_or_repeats():
    rows = [_row(i) for i in range(95)]
    seen = []
    for offset in range(0, 95, 40):
        page, total, pending = rq.page_rows(rows, {"sort": "title_asc"}, TODAY, offset, 40)
        seen += [row["id"] for row in page]
        assert total == 95 and len(pending) == 95
    assert sorted(seen) == sorted(row["id"] for row in rows) and len(seen) == len(set(seen))


def test_no_cap_hides_part_of_a_large_queue():
    rows = [_row(i) for i in range(12000)]
    _page, total, pending = rq.page_rows(rows, {}, TODAY, 11990, 40)
    assert total == 12000 and len(pending) == 12000


def test_filters_match_the_browser_rules():
    rows = [
        _row(1, type="anime", release_date="2027-01-01", genres=["Fantasy"], rating=7.1),
        _row(2, type="show", release_date="2020-05-01", genres=["Drama"], rating=6.0),
        _row(3, type="movie", year=2030, rating=None),
        _row(4, type="movie", year=None, release_date=None),
    ]
    ids = lambda params: [row["id"] for row in rq.page_rows(rows, params, TODAY, 0, 40)[0]]
    assert ids({"types": "anime"}) == ["r1"]
    assert ids({"types": "tv"}) == ["r2"]
    assert ids({"release": "upcoming"}) == ["r1", "r3"]
    assert ids({"genre": "Fantasy"}) == ["r1"]
    assert ids({"from_rating": "7.1", "to_rating": "7.1"}) == ["r1"]
    assert ids({"q": "title 002"}) == ["r2"]
    assert ids({"from_year": "2025"}) == ["r3"]
    assert rq.release_bucket(rows[3], TODAY) == "unknown"


def test_sorts():
    rows = [_row(1, match_score=50, release_date="2021-01-01"), _row(2, match_score=90),
            _row(3, updated_at="", created_at="", release_date="2025-01-01")]
    order = lambda sort: [row["id"] for row in rq.page_rows(rows, {"sort": sort}, TODAY, 0, 40)[0]]
    assert order("match_desc")[0] == "r2"
    assert order("added_asc")[-1] == "r3"  # undated last, never first
    assert order("release_desc")[0] == "r3"
    assert order(None) == ["r1", "r2", "r3"]  # unknown sort keeps the base order


def test_facets_count_the_whole_tab():
    rows = [_row(1, type="anime", genres=["Action"]), _row(2, type="show", genres=["Drama", "Action"]),
            _row(3, delivery_status="not_delivered")]
    facets = rq.facets(rows, TODAY)
    assert facets["genres"] == ["Action", "Drama"]
    assert facets["type_counts"] == {"anime": 1, "tv": 1, "movie": 1}
    assert facets["kind_counts"] == {"movie": 1, "show": 1, "anime": 1}
    assert facets["undelivered"] == 1
