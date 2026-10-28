"""Instagram fake follower checker, giveaway picker, audit and tracker."""

from .audit import AuditReport, audit_account
from .client import Usage, open_client
from .fake_followers import FakeFollowerReport, check_fake_followers
from .followers import FollowerDiff, SnapshotStore, track_followers
from .giveaway import GiveawayResult, GiveawayRules, run_giveaway
from .instagram import (
    PrivateProfileError,
    Profile,
    get_post,
    get_public_profile,
)

__version__ = "0.1.0"

__all__ = [
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
