"""Who may download mission content by hash over /Marti/sync/content
(raven/blueprints/marti_api/data_package_marti_api.py).

The rule matches listing and subscribing to the mission: a device that can see and subscribe to a
mission must be able to fetch its content, whichever direction its group membership has. Reuses the
mission app fixture of tests/test_mission_features.py.
"""

import datetime
import importlib

import pytest

from raven.extensions import db
from tests.test_mission_features import MISSION, PHOTO, PHOTO_HASH, app  # noqa: F401

dp_marti = importlib.import_module("raven.blueprints.marti_api.data_package_marti_api")


class _FakeCert:
    def __init__(self, cn):
        self._cn = cn

    def get_subject(self):
        return type("Subject", (), {"commonName": self._cn})()


@pytest.fixture
def grouped(app, monkeypatch):  # noqa: F811
    """The mission is in group "red"; bob and the anonymous caller aren't in it."""
    from raven.models.EUD import EUD
    from raven.models.Group import Group
    from raven.models.GroupMission import GroupMission

    app.register_blueprint(dp_marti.data_package_marti_api)
    with app.app_context():
        red = Group()
        red.name = "red"
        red.type = Group.SYSTEM
        db.session.add(red)
        db.session.flush()
        db.session.add(GroupMission(group_id=red.id, mission_name=MISSION))
        alice = app.security.datastore.find_user(username="alice")
        db.session.add(EUD(uid="ANDROID-alice", callsign="ALICE", user_id=alice.id))
        db.session.commit()

    def as_cert(cn):
        monkeypatch.setattr(dp_marti, "verify_client_cert", (lambda: _FakeCert(cn)) if cn else (lambda: False))

    return as_cert


def _membership(app, direction, enabled=True, expires_at=None):
    from raven.models.Group import Group
    from raven.models.GroupUser import GroupUser

    with app.app_context():
        alice = app.security.datastore.find_user(username="alice")
        red = db.session.query(Group).filter_by(name="red").one()
        db.session.add(
            GroupUser(user_id=alice.id, group_id=red.id, direction=direction, enabled=enabled, expires_at=expires_at)
        )
        db.session.commit()


def _role(app, client_uid, username):
    from raven.models.MissionRole import MissionRole

    with app.app_context():
        db.session.add(
            MissionRole(
                clientUid=client_uid,
                username=username,
                createTime=datetime.datetime(2026, 11, 1),
                role_type=MissionRole.MISSION_SUBSCRIBER,
                mission_name=MISSION,
            )
        )
        db.session.commit()


def _get(app):
    return app.test_client().get("/Marti/sync/content", query_string={"hash": PHOTO_HASH})


def _head(app):
    return app.test_client().head("/Marti/sync/content", query_string={"hash": PHOTO_HASH})


def test_outsiders_are_refused(app, grouped):  # noqa: F811
    grouped(None)
    assert _get(app).status_code == 403 and _head(app).status_code == 403
    grouped("bob")
    assert _get(app).status_code == 403 and _head(app).status_code == 403


def test_in_only_member_can_download(app, grouped):  # noqa: F811
    grouped("alice")
    _membership(app, "IN")
    r = _get(app)
    assert r.status_code == 200 and r.data == PHOTO
    assert _head(app).status_code == 200


def test_subscribed_in_only_member_with_disabled_out_can_download(app, grouped):  # noqa: F811
    grouped("alice")
    _membership(app, "OUT", enabled=False)
    # A disabled OUT membership alone grants nothing
    assert _get(app).status_code == 403
    _membership(app, "IN")
    _role(app, "ANDROID-alice", "ANDROID-alice")
    assert _get(app).status_code == 200


def test_subscription_alone_grants_access(app, grouped):  # noqa: F811
    grouped("alice")
    _membership(app, "OUT", expires_at=datetime.datetime(2000, 1, 1))
    # An expired membership grants nothing
    assert _get(app).status_code == 403
    # Subscribed by device uid
    _role(app, "ANDROID-alice", "ANDROID-alice")
    assert _get(app).status_code == 200


def test_subscription_by_username(app, grouped):  # noqa: F811
    grouped("alice")
    _role(app, "SOME-OTHER-UID", "ALICE")
    assert _get(app).status_code == 200


def test_anon_group_mission_is_open(app, grouped):  # noqa: F811
    from raven.models.Group import Group
    from raven.models.GroupMission import GroupMission

    with app.app_context():
        anon = Group()
        anon.name = "__ANON__"
        anon.type = Group.SYSTEM
        db.session.add(anon)
        db.session.flush()
        db.session.add(GroupMission(group_id=anon.id, mission_name=MISSION))
        db.session.commit()
    grouped(None)
    assert _get(app).status_code == 200


def test_admin_can_download(app, grouped):  # noqa: F811
    grouped("admin")
    assert _get(app).status_code == 200
