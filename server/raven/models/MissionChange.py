import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from xml.etree.ElementTree import Element, SubElement

from bs4 import BeautifulSoup
from sqlalchemy import JSON, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.functions import iso8601_string_from_datetime
from raven.models.columns import owned_by, ref, text
from raven.models.serialization import attribute_of, columns, iso

# Snapshot column -> Marti key
_SNAPSHOTS = {
    "external_data": "externalData",
    "mission_feed": "missionFeed",
    "mission_layer": "missionLayer",
}


@dataclass
class MissionChange(db.Model):
    """One entry in a mission's change log, as TAK Server reports it in MissionChanges."""

    __tablename__ = "mission_changes"

    CREATE_MISSION = "CREATE_MISSION"
    DELETE_MISSION = "DELETE_MISSION"
    ADD_CONTENT = "ADD_CONTENT"
    REMOVE_CONTENT = "REMOVE_CONTENT"
    CREATE_DATA_FEED = "CREATE_DATA_FEED"
    DELETE_DATA_FEED = "DELETE_DATA_FEED"
    CHANGE = "CHANGE"
    ADD_MISSION_LAYER = "ADD_MISSION_LAYER"
    UPDATE_MISSION_LAYER = "UPDATE_MISSION_LAYER"
    REMOVE_MISSION_LAYER = "REMOVE_MISSION_LAYER"

    # TAK Server has no change types for layers: these are shown in the web UI only
    MARTI_HIDDEN_TYPES = (ADD_MISSION_LAYER, UPDATE_MISSION_LAYER, REMOVE_MISSION_LAYER)

    id: Mapped[int] = mapped_column(primary_key=True)
    content_uid: Mapped[str | None] = text(ref("mission_content.uid"))
    isFederatedChange: Mapped[bool] = mapped_column()
    change_type: Mapped[str] = text()
    mission_name: Mapped[str] = text(ref("missions.name"))
    timestamp: Mapped[datetime] = mapped_column()
    creator_uid: Mapped[str] = text()
    server_time: Mapped[datetime] = mapped_column()
    mission_uid: Mapped[str | None] = text(owned_by("mission_uids.uid"))
    # What the changed item's to_json() returned when the change was made
    external_data: Mapped[dict | None] = mapped_column(JSON)
    mission_feed: Mapped[dict | None] = mapped_column(JSON)
    mission_layer: Mapped[dict | None] = mapped_column(JSON)

    mission = relationship("Mission", back_populates="mission_changes")
    uid = relationship("MissionUID", back_populates="mission_change", uselist=False)
    content_resource = relationship(
        "MissionContent", back_populates="mission_changes", uselist=False
    )

    def serialize(self):
        change = columns(
            self, ("isFederatedChange", "change_type", "mission_name", "timestamp", "creator_uid")
        )
        change.update(columns(self, ("server_time", "mission_uid", "content_uid")))
        change.update(columns(self, _SNAPSHOTS))
        return change

    def hidden_from_marti(self) -> bool:
        return self.change_type in self.MARTI_HIDDEN_TYPES

    def _content_resource_json(self):
        resource = self.content_resource.to_json()["data"]
        resource["contentHash"] = self.content_resource.hash
        return resource

    def to_json(self):
        # Clients look the change up by the uid they submitted: the content's, else the item's
        subject = self.mission_uid if self.content_uid is None else self.content_uid
        change = {
            "isFederatedChange": self.isFederatedChange,
            "type": self.change_type,
            "contentUid": subject,
            "missionName": self.mission_name,
            "timestamp": iso(self.timestamp),
            "creatorUid": self.creator_uid or "",
            "serverTime": iso(self.server_time),
            "missionGuid": attribute_of(self.mission, "guid"),
        }
        if self.content_resource:
            change["contentResource"] = self._content_resource_json()
        if self.uid:
            change["details"] = self.uid.to_details_json()
        # Item snapshots appear only when there is one
        for column, key in _SNAPSHOTS.items():
            snapshot = getattr(self, column)
            if snapshot:
                change[key] = snapshot
        return change


def _text_child(parent, tag, text):
    child = SubElement(parent, tag)
    child.text = text
    return child


def generate_mission_change_cot(
    mission_name: str,
    mission=None,
    mission_change: MissionChange = None,
    content=None,
    cot_event: BeautifulSoup | None = None,
    mission_uid=None,
    cot_type: str = "t-x-m-c",
) -> Element:
    """Build the t-x-m-c CoT that tells subscribers about one mission change."""
    if mission is None:
        from raven.models.Mission import Mission

        mission = db.session.execute(
            db.select(Mission).filter_by(name=mission_name)
        ).scalar_one_or_none()

    if cot_event is not None and cot_event.name == "[document]":
        event_tag = cot_event.find("event")
        if event_tag is not None:
            cot_event = event_tag

    if content is not None:
        event_uid = content.uid
    elif cot_event is not None and cot_event.attrs.get("uid"):
        event_uid = cot_event.attrs.get("uid")
    else:
        event_uid = str(uuid.uuid4())

    timestamp = mission_change.timestamp
    when = iso8601_string_from_datetime(timestamp)
    stale = iso8601_string_from_datetime(timestamp + timedelta(minutes=2))

    event = Element(
        "event",
        {
            "version": "2.0",
            "uid": event_uid,
            "type": cot_type,
            "how": "h-g-i-g-o",
            "start": when,
            "time": when,
            "stale": stale,
            "access": "Undefined",
        },
    )
    SubElement(
        event, "point", {"ce": "9999999", "le": "9999999", "hae": "0.0", "lat": "0.0", "lon": "0.0"}
    )
    detail = SubElement(event, "detail")
    mission_element = SubElement(
        detail,
        "mission",
        {
            "type": "CHANGE",
            "tool": (mission.tool if mission else None) or "public",
            "name": mission_name,
            "guid": str(mission.guid if mission else None),
            "authorUid": str(mission_change.creator_uid),
        },
    )
    changes = SubElement(mission_element, "MissionChanges")
    change = SubElement(changes, "MissionChange")

    if content is not None:
        resource = SubElement(change, "contentResource")
        _text_child(resource, "creatorUid", mission_change.creator_uid)
        _text_child(resource, "expiration", "-1")
        _text_child(resource, "groupVector", "0")
        _text_child(resource, "hash", content.hash)
        _text_child(resource, "mimeType", content.mime_type)
        _text_child(resource, "name", content.filename)
        _text_child(resource, "size", str(content.size) if content.size is not None else None)
        _text_child(
            resource, "submissionTime", iso8601_string_from_datetime(content.submission_time)
        )
        _text_child(resource, "submitter", content.submitter)
        _text_child(resource, "uid", content.uid)
        _text_child(change, "contentUid", mission_change.content_uid)

    if cot_event is not None:
        details = SubElement(change, "details", {"type": cot_event.attrs.get("type", "")})
        color = cot_event.find("color")
        if color is not None:
            color_value = color.attrs.get("argb")
            if color.attrs.get("value"):
                color_value = color.attrs.get("value")
            if color_value is not None:
                details.set("color", color_value)
        contact = cot_event.find("contact")
        if contact is not None and contact.attrs.get("callsign"):
            details.set("callsign", contact.attrs.get("callsign"))
        usericon = cot_event.find("usericon")
        if usericon is not None:
            iconset_path = usericon.attrs.get("iconsetpath") or usericon.attrs.get("iconsetPath")
            if iconset_path:
                details.set("iconsetPath", iconset_path)
        point = cot_event.find("point")
        if point is not None and point.attrs.get("lat") and point.attrs.get("lon"):
            SubElement(
                details, "location", {"lon": point.attrs.get("lon"), "lat": point.attrs.get("lat")}
            )

    if mission_uid is not None:
        details = SubElement(change, "details")
        if mission_uid.color:
            details.set("color", str(mission_uid.color))
        if mission_uid.callsign:
            details.set("callsign", mission_uid.callsign)
        if cot_type:
            details.set("type", cot_type)
        if mission_uid.iconset_path:
            details.set("iconsetPath", mission_uid.iconset_path)
        if mission_uid.longitude:
            SubElement(
                details,
                "location",
                {"lon": str(mission_uid.longitude), "lat": str(mission_uid.latitude)},
            )

    if mission_change.mission_uid:
        _text_child(change, "contentUid", mission_change.mission_uid)

    if mission_change.external_data:
        external = SubElement(change, "externalData")
        for key in ("id", "name", "notes", "tool", "urlData", "urlView"):
            _text_child(external, key, str(mission_change.external_data.get(key) or ""))

    if mission_change.mission_feed:
        feed = SubElement(change, "missionFeed")
        for key in ("uid", "dataFeedUid", "name", "filterBbox", "filterType", "filterCallsign"):
            _text_child(feed, key, str(mission_change.mission_feed.get(key) or ""))

    _text_child(change, "missionGuid", mission.guid if mission else None)
    _text_child(change, "creatorUid", mission_change.creator_uid)
    _text_child(
        change, "isFederatedChange", "true" if mission_change.isFederatedChange else "false"
    )
    _text_child(change, "missionName", mission.name if mission else mission_name)
    _text_child(change, "timestamp", when)
    _text_child(change, "type", mission_change.change_type)

    return event


def upsert_mission_uid_and_change(
    mission_name,
    mission,
    item_uid,
    creator_uid,
    timestamp,
    cot_type=None,
    callsign=None,
    iconset_path=None,
    color=None,
    latitude=None,
    longitude=None,
):
    """Record that a map item was added to (or re-synced into) a mission: exactly one MissionUID
    and one ADD_CONTENT MissionChange per (mission, item uid). Returns the MissionUID, the
    MissionChange and the change CoT; the caller publishes the CoT."""
    from raven.models.MissionUID import MissionUID

    optional = {
        "cot_type": cot_type,
        "callsign": callsign,
        "iconset_path": iconset_path,
        "color": color,
        "latitude": latitude,
        "longitude": longitude,
    }
    supplied = {key: value for key, value in optional.items() if value is not None}

    row = db.session.execute(
        db.select(MissionUID).filter_by(uid=item_uid, mission_name=mission_name)
    ).scalar_one_or_none()
    try:
        if row is None:
            row = MissionUID()
            db.session.add(row)
        row.uid = item_uid
        row.mission_name = mission_name
        row.timestamp = timestamp
        row.creator_uid = creator_uid
        for key, value in supplied.items():
            setattr(row, key, value)
        db.session.commit()
    except IntegrityError:
        # The uid already exists, probably under another mission (uid alone is the key): move it here
        db.session.rollback()
        values = {
            "mission_name": mission_name,
            "timestamp": timestamp,
            "creator_uid": creator_uid,
            **supplied,
        }
        db.session.execute(update(MissionUID).where(MissionUID.uid == item_uid).values(**values))
        db.session.commit()
        row = db.session.execute(db.select(MissionUID).filter_by(uid=item_uid)).scalar_one()

    change = (
        db.session.execute(
            db.select(MissionChange).filter_by(
                mission_uid=item_uid,
                mission_name=mission_name,
                change_type=MissionChange.ADD_CONTENT,
            )
        )
        .scalars()
        .first()
    )
    if change is None:
        change = MissionChange()
        change.isFederatedChange = False
        change.mission_name = mission_name
        change.mission_uid = item_uid
        db.session.add(change)
    change.change_type = MissionChange.ADD_CONTENT
    change.creator_uid = creator_uid
    change.timestamp = timestamp
    change.server_time = datetime.now(timezone.utc)
    db.session.commit()

    event = generate_mission_change_cot(mission_name, mission, change, mission_uid=row)
    return row, change, event
