"""
Who may change or delete a map item (marker, CASEVAC) from the web API.

These rows don't record which web user made them: an item from a device carries that device's
uid (Marker.parent_uid, CasEvac.sender_uid), and one made on the web page carries the server's
own node id. So the owner is the user the sending device belongs to, and items the server sent
(web-made, or from a device that belongs to nobody) are administrators' to change.
"""

from raven.extensions import db
from raven.group_isolation import is_admin


def owns_eud(user, eud_uid) -> bool:
    """True if the EUD with this uid belongs to `user`."""
    from raven.models.EUD import EUD

    if not eud_uid or user is None or getattr(user, "id", None) is None:
        return False
    eud = db.session.query(EUD).filter_by(uid=eud_uid).first()
    return eud is not None and eud.user_id is not None and eud.user_id == user.id


def can_modify_device_item(user, sender_uid) -> bool:
    """An administrator, or the owner of the device that sent the item."""
    return is_admin(user) or owns_eud(user, sender_uid)
