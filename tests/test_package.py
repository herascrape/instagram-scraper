import instagram_scrapers


def test_public_api_is_importable() -> None:
    assert sorted(instagram_scrapers.__all__) == sorted(
        [
            "AuditReport",
            "FakeFollowerReport",
            "FollowerDiff",
            "GiveawayResult",
            "GiveawayRules",
            "PrivateProfileError",
            "Profile",
            "SnapshotStore",
            "Usage",
            "__version__",
            "audit_account",
            "check_fake_followers",
            "get_post",
            "get_public_profile",
            "open_client",
            "run_giveaway",
            "track_followers",
        ]
    )
    for name in instagram_scrapers.__all__:
        assert getattr(instagram_scrapers, name) is not None
