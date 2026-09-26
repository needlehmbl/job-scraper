from api.routes.admin import needs_update


def test_dev_checkout_is_not_told_to_upgrade():
    # A source checkout has no release version baked in. The banner used to
    # read "update available" forever, because "v0.2.3" != "dev" can never be
    # false -- even when dev is already sitting on the tagged commit.
    assert needs_update("dev", "v0.2.3") is False


def test_missing_version_is_not_told_to_upgrade():
    assert needs_update("", "v0.2.3") is False


def test_older_release_is_told_to_upgrade():
    assert needs_update("v0.2.2", "v0.2.3") is True


def test_current_release_is_not_told_to_upgrade():
    assert needs_update("v0.2.3", "v0.2.3") is False


def test_unreachable_latest_is_not_told_to_upgrade():
    assert needs_update("v0.2.2", None) is False
