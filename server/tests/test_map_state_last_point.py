"""/api/map_state sends each EUD's newest own position, so the map shows users at once."""

from datetime import datetime, timedelta, timezone

from raven.extensions import db
from raven.models.EUD import EUD
from raven.models.Point import Point

# A logged-in administrator client on the shared app fixture
from tests.test_fedhub_names import admin  # noqa: F401


def _point(uid, device_uid, lat, minutes_ago):
    return Point(
        uid=uid,
        device_uid=device_uid,
        latitude=lat,
        longitude=150.9,
        ce=9999999.0,
        hae=0.0,
        le=9999999.0,
        timestamp=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )


def test_map_state_includes_newest_own_point(app, admin):  # noqa: F811
    with app.app_context():
        db.session.add_all([EUD(uid="ANDROID-a", callsign="ALPHA"), EUD(uid="ANDROID-b", callsign="BRAVO")])
        db.session.commit()
        db.session.add_all([
            _point("ANDROID-a", "ANDROID-a", -33.0, minutes_ago=10),
            _point("ANDROID-a", "ANDROID-a", -33.5, minutes_ago=1),
            # A marker ALPHA dropped later: its position, not ALPHA's
            _point("marker-1", "ANDROID-a", -40.0, minutes_ago=0),
        ])
        db.session.commit()

    euds = {e["uid"]: e for e in admin.get("/api/map_state").json["euds"]}
    assert euds["ANDROID-a"]["last_point"]["latitude"] == -33.5
    assert euds["ANDROID-a"]["last_point"]["uid"] == "ANDROID-a"
    # Never reported a position: nothing to place yet
    assert euds["ANDROID-b"]["last_point"] is None


def test_points_have_the_device_time_index():
    names = {index.name for index in Point.__table__.indexes}
    assert "ix_points_device_uid_timestamp" in names
