"""Regression tests for the taste signals fixed on 2026-09-24 (HANDOFF.md, omgång 4)."""

from providers.live_history import overlay_rows
from recommendation.pipeline import MIN_LIKED_FOR_FLOOR, TASTE_FLOOR, run_pipeline
from recommendation.ranking_engine import score_candidates
from recommendation.taste_engine import build_taste_snapshot, taste_genres


def _trakt(title, year, rating=None, plays=1, **extra):
    rows = []
    for index in range(plays):
        rows.append({
            "title": title, "year": year, "type": "show", "source": "trakt", "provider": "trakt",
            "canonical_media_id": "mid_%s" % title.replace(" ", "_").lower(),
            "tmdb_id": abs(hash(title)) % 100000, "trakt_id": abs(hash(title)) % 90000,
            "watched_at": "2026-0%d-01T00:00:00Z" % (1 + index % 8), "rating": rating,
            "genres": ["Action", "Adventure"], **extra,
        })
    return rows


def test_a_title_on_two_providers_keeps_its_rating_when_one_provider_is_switched_off():
    # Decided per row now. The merged title used to be filed under its
    # alphabetically first provider ("anilist") and vanished from a job
    # without AniList, Trakt rating and all.
    history = _trakt("Frieren", 2023, rating=10) + [{
        "title": "Frieren", "year": 2023, "type": "anime", "source": "anilist", "provider": "anilist",
        "canonical_media_id": "mid_frieren", "anilist_id": 154587, "status": "CURRENT",
    }]
    taste = build_taste_snapshot(history, taste_sources=["trakt"])
    assert [row["title"] for row in taste["liked_titles"]] == ["Frieren"]
    assert taste["liked_titles"][0]["rating"] == 10


def test_plan_to_watch_on_one_provider_does_not_erase_hundreds_of_episodes_on_another():
    history = [{
        "title": "Family Guy", "year": 1999, "type": "show", "source": "simkl", "provider": "simkl",
        "canonical_media_id": "mid_family_guy", "status": "plantowatch", "simkl_id": 1,
    }] + _trakt("Family Guy", 1999, rating=10, plays=40)
    taste = build_taste_snapshot(history)
    assert taste["liked_titles"] and taste["liked_titles"][0]["title"] == "Family Guy"
    assert taste["ignored_rows"]["plan_to_watch"] == 1


def test_demo_shelf_rows_never_reach_the_profile():
    personal = [
        {"title": "Chernobyl", "year": 2019, "type": "show", "provider": "simkl", "rating": 9.4, "rating_scale": 10,
         "canonical_media_id": "mid_chernobyl"},
        # The same title with a real provider id and a whole-number rating is real.
        {"title": "Severance", "year": 2022, "type": "show", "provider": "trakt", "rating": 9, "rating_scale": 10,
         "canonical_media_id": "mid_severance", "trakt_id": 150, "imdb_id": "tt11280740"},
    ]
    taste = build_taste_snapshot([], personal_history=personal)
    assert [row["title"] for row in taste["liked_titles"]] == ["Severance"]
    assert taste["ignored_rows"]["demo_seed"] == 1


def test_requests_decisions_are_taste_evidence_but_a_watched_rejection_is_not_a_dislike():
    history = _trakt("Logan", 2017, rating=10)
    requests = [
        {"title": "CSI: Crime Scene Investigation", "year": 2000, "type": "show", "status": "rejected",
         "tmdb_id": 1431, "genres": ["Crime", "Drama"]},
        {"title": "Logan", "year": 2017, "type": "show", "status": "rejected",
         "tmdb_id": history[0]["tmdb_id"], "genres": ["Action"]},
        {"title": "Dune: Prophecy", "year": 2024, "type": "show", "status": "approved",
         "tmdb_id": 90228, "genres": ["Sci-Fi & Fantasy", "Drama"]},
        {"title": "Pending Thing", "year": 2026, "type": "show", "status": "pending_approval", "tmdb_id": 5},
    ]
    taste = build_taste_snapshot(history, requests=requests)
    negatives = [row["title"] for row in taste["negative_titles"]]
    assert negatives == ["CSI: Crime Scene Investigation"]
    assert taste["rejected_but_watched"] == 1
    assert "Dune: Prophecy" in [row["title"] for row in taste["liked_titles"]]
    assert taste["approved_count"] == 1 and taste["rejected_count"] == 1


def test_genres_are_one_vocabulary_across_providers():
    assert taste_genres({"genres": ["Science-Fiction"]}) == ["sci-fi"]
    assert taste_genres({"genres": ["Sci-Fi"]}) == ["sci-fi"]
    assert taste_genres({"genres": ["Sci-Fi & Fantasy", "Action & Adventure"]}) == ["sci-fi", "fantasy", "action", "adventure"]
    # TMDb's combined TV ids are not expanded for taste (measured; see taste_engine).
    assert taste_genres({"genres": ["Sci-Fi"], "tmdb_genre_ids": [10765]}) == ["sci-fi"]


def _profile(count=MIN_LIKED_FOR_FLOOR + 2):
    history = []
    for index in range(count):
        history += _trakt("Hero Show %d" % index, 2015 + index % 8, rating=10, plays=12,
                          tmdb_keywords=["superhero", "based on comic", "vigilante"],
                          creators=["Greg Berlanti"], original_language="en")
    return history


def test_match_score_is_about_the_viewer_not_the_format_or_the_language():
    taste = build_taste_snapshot(_profile())
    base = {"type": "show", "media_type": "tv", "genres": ["Comedy"], "tmdb_keywords": ["sitcom"],
            "vote_count": 500, "tmdb_rating": 7.5, "popularity": 50}
    rows = score_candidates([
        {**base, "title": "English Sitcom", "year": 2010, "original_language": "en"},
        {**base, "title": "German Sitcom", "year": 2010, "original_language": "de"},
    ], taste)
    english, german = (next(row for row in rows if row["title"] == name) for name in ("English Sitcom", "German Sitcom"))
    # The language preference moves the order by a lot; the match percentage only
    # by the small language share inside "resembles a liked title".
    assert english["rank_score"] - german["rank_score"] >= 1.0
    assert abs(english["match_score"] - german["match_score"]) <= 5
    # Neither shares anything specific with a superhero profile: not a match.
    assert english["match_score"] < 50 and english["personal_score"] < TASTE_FLOOR


def test_a_job_returns_fewer_picks_rather_than_titles_with_no_link_to_the_history():
    history = _profile()
    candidates = [
        {"title": "Vigilante Nights", "year": 2024, "type": "show", "media_type": "tv", "source": "tmdb_similar",
         "genres": ["Action", "Adventure"], "tmdb_keywords": ["superhero", "based on comic", "vigilante"],
         "creators": ["Greg Berlanti"], "original_language": "en", "vote_count": 900, "tmdb_rating": 7.8},
        {"title": "Three's Company", "year": 1977, "type": "show", "media_type": "tv", "source": "tmdb_discover",
         "genres": ["Comedy"], "tmdb_keywords": ["sitcom", "roommates"], "original_language": "en",
         "vote_count": 400, "tmdb_rating": 7.4, "popularity": 60},
    ]
    result = run_pipeline({"job_type": "personalized", "media_types": ["tv"], "final_recommendation_limit": 10},
                          history=history, catalog=[], extra_candidates=candidates)
    assert result["taste_floor"] == TASTE_FLOOR
    assert [row["title"] for row in result["accepted"]] == ["Vigilante Nights"]
    assert result["below_taste_floor"] == 1
    pick = result["accepted"][0]
    assert "Hero Show" in pick["why"]  # the reason names the history it matched


def test_live_overlay_adds_what_the_synced_copy_is_missing_and_fixes_the_anilist_scale():
    history = _trakt("Arrow", 2012, rating=10)
    personal = [
        {"title": "Arrow", "year": 2012, "type": "show", "provider": "trakt", "rating": 10, "rating_scale": 10,
         "canonical_media_id": history[0]["canonical_media_id"], "tmdb_id": history[0]["tmdb_id"]},
        # Stored from a POINT_3 list: 3 is the best smiley, not 3/10.
        {"title": "Digimon", "year": 1999, "type": "anime", "provider": "anilist", "rating": 3, "rating_scale": 10,
         "canonical_media_id": "mid_digimon", "anilist_id": 2},
    ]
    trakt = {
        "ratings": [
            {"title": "Arrow", "year": 2012, "type": "show", "tmdb_id": history[0]["tmdb_id"], "rating": 10.0,
             "rating_scale": 10, "provider": "trakt", "source": "trakt"},
            {"title": "The Witcher", "year": 2019, "type": "show", "tmdb_id": 71912, "rating": 10.0,
             "rating_scale": 10, "provider": "trakt", "source": "trakt"},
        ],
        "watched": [
            {"title": "The Witcher", "year": 2019, "type": "show", "tmdb_id": 71912, "progress": 24,
             "provider": "trakt", "source": "trakt"},
        ],
    }
    anilist = [{"title": "Digimon", "year": 1999, "type": "anime", "anilist_id": 2, "rating": 8,
                "rating_scale": 10, "provider": "anilist", "source": "anilist"}]
    extra_history, extra_personal, report = overlay_rows(history, personal, trakt, anilist)
    assert [row["title"] for row in extra_history] == ["The Witcher"]
    assert {row["title"] for row in extra_personal} == {"The Witcher", "Digimon"}
    digimon = next(row for row in extra_personal if row["title"] == "Digimon")
    assert digimon["rating"] == 8 and digimon["canonical_media_id"] == "mid_digimon"
    assert report["trakt"] == {"live_ratings": 2, "ratings_added": 1, "live_watched": 1, "watched_added": 1}
    taste = build_taste_snapshot(history + extra_history, personal_history=personal + extra_personal)
    assert "Digimon" not in [row["title"] for row in taste["negative_titles"]]


def test_rows_stay_storable_after_the_rerank_prompt_is_built():
    # 2026-09-24 21:30 UTC: every AI-reranked run failed with "cannot encode
    # object ... set", because building the prompt's evidence cached a set on
    # the ranked rows that were then saved.
    import bson

    from jobs.engine import rerank_lines
    from recommendation.ranking_engine import apply_rerank, strip_private

    taste = build_taste_snapshot(_profile())
    rows = score_candidates([
        {"title": "Vigilante Nights", "year": 2024, "type": "show", "media_type": "tv", "candidate_id": "v",
         "genres": ["Action"], "tmdb_keywords": ["superhero", "vigilante"], "creators": ["Greg Berlanti"],
         "original_language": "en"},
        {"title": "Other Show", "year": 2023, "type": "show", "media_type": "tv", "candidate_id": "o",
         "genres": ["Drama"], "tmdb_keywords": ["family"], "original_language": "en"},
    ], taste)
    handles, lines = rerank_lines(rows, taste)
    assert any("evidence=" in line for line in lines)
    for row in apply_rerank(rows, ["o", "v"]) + [strip_private(row) for row in rows]:
        bson.BSON.encode(row)


def test_a_keyword_that_only_restates_a_genre_is_not_a_concrete_link():
    from recommendation.similarity import explain_match, specific_link

    liked = {"title": "Amphibia", "year": 2019, "genres": ["Animation", "Fantasy", "Comedy"],
             "tmdb_keywords": ["fantasy", "comedy", "frog", "magic"]}
    genre_words_only = {"title": "Sodor516", "year": 2028, "genres": ["Comedy", "Fantasy"],
                        "tmdb_keywords": ["fantasy", "comedy", "giallo"]}
    real_theme = {"title": "Frog Tales", "year": 2026, "genres": ["Animation"], "tmdb_keywords": ["frog", "magic"]}
    assert specific_link(genre_words_only, liked) == 0.0
    assert not any(part.startswith("themes") for part in explain_match(genre_words_only, liked))
    assert specific_link(real_theme, liked) > 0.0
