from football_analytics.services.pitchapi_match_sync import _player_values, _team_stat_values


def test_pitchapi_reads_on_target_shots_from_display_label_key() -> None:
    values = _player_values(
        {
            "stats": [
                {
                    "stats": {
                        "Shots on target": {
                            "key": "ShotsOnTarget",
                            "stat": {"type": "integer", "value": 2},
                        },
                        "Total shots": {
                            "key": "total_shots",
                            "stat": {"type": "integer", "value": 5},
                        },
                    }
                }
            ]
        }
    )

    assert values["shots_total"] == 5
    assert values["shots_on_target"] == 2


def test_pitchapi_extracts_team_shots_possession_and_published_metrics() -> None:
    values = _team_stat_values(
        {
            "groups": [
                {
                    "items": [
                        {"key": "BallPossesion", "home": "59", "away": "41"},
                        {"key": "total_shots", "home": "19", "away": "9"},
                        {"key": "ShotsOnTarget", "home": "6", "away": "2"},
                        {"key": "ShotsOffTarget", "home": "9", "away": "6"},
                        {"key": "blocked_shots", "home": "4", "away": "1"},
                        {"key": "shots_inside_box", "home": "13", "away": "7"},
                        {"key": "shots_outside_box", "home": "6", "away": "2"},
                        {"key": "corners", "home": "3", "away": "1"},
                        {"key": "fouls", "home": "13", "away": "9"},
                        {"key": "Offsides", "home": "6", "away": "2"},
                        {"key": "yellow_cards", "home": "0", "away": "0"},
                        {"key": "red_cards", "home": "0", "away": "0"},
                        {"key": "passes", "home": "631", "away": "432"},
                        {"key": "accurate_passes", "home": "574 (91%)", "away": "367 (85%)"},
                        {"key": "keeper_saves", "home": "2", "away": "4"},
                    ]
                }
            ]
        }
    )

    assert values["home"] == {
        "possession": 59,
        "shots_total": 19,
        "shots_on_target": 6,
        "shots_off_target": 9,
        "shots_blocked": 4,
        "shots_inside_box": 13,
        "shots_outside_box": 6,
        "corners": 3,
        "fouls": 13,
        "offsides": 6,
        "yellow_cards": 0,
        "red_cards": 0,
        "passes_total": 631,
        "passes_accurate": 574,
        "goalkeeper_saves": 2,
    }
    assert values["away"]["shots_total"] == 9
    assert values["away"]["possession"] == 41
    assert values["away"]["passes_accurate"] == 367
