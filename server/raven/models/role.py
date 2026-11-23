from flask_security.models import fsqla_v3 as fsqla

from raven.extensions import db


class Role(db.Model, fsqla.FsRoleMixin):
    """A Flask-Security role, e.g. administrator, user or admin_viewer."""

    __tablename__ = "role"

    def serialize(self):
        role = {"name": self.name, "description": self.description}
        role["permissions"] = [*(self.permissions or ())]
        # Exposed under a different key from the column
        role["update_timestamp"] = self.update_datetime
        return role

    def __eq__(self, other):
        # Equal to a role name, or to anything with the same .name
        if isinstance(other, str):
            return self.name == other
        return self.name == getattr(other, "name", None)

    def __ne__(self, other):
        return not self.__eq__(other)

    __hash__ = None
