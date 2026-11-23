"""
LDAP sign-in for the web UI, and the save-user callback that mirrors a user's LDAP groups into
local Flask-Security roles. Only used when RAVEN_ENABLE_LDAP is on.
"""

from flask import Blueprint
from flask import current_app as app
from flask import request
from flask_babel import gettext
from flask_ldap3_login.forms import LDAPLoginForm
from flask_wtf.csrf import generate_csrf
from flask_security import login_user
from flask_security.utils import base_render_json

from raven.extensions import ldap_manager
from raven.turnstile import turnstile_passed

ldap_blueprint = Blueprint("ldap_blueprint", __name__)


@ldap_manager.save_user
def save_user(dn: str, username: str, data, groups):
    """Finds or creates the local user and replaces its roles with those its LDAP groups give it."""
    datastore = app.security.datastore
    user = datastore.find_user(username=username)
    if user:
        for role in list(user.roles):
            datastore.remove_role_from_user(user, role)
    else:
        user = datastore.create_user(username=username, password=None)

    admin_group = app.config.get("RAVEN_LDAP_ADMIN_GROUP")
    prefix = app.config.get("RAVEN_LDAP_GROUP_PREFIX")
    is_admin = False
    for group in groups or []:
        cn = group.get("cn")
        if not cn:
            continue
        if cn == admin_group:
            is_admin = True
        elif cn.startswith(prefix) and not cn.lower().endswith(("_read", "_write")):
            datastore.add_role_to_user(user, datastore.find_or_create_role(cn))

    datastore.add_role_to_user(user, datastore.find_or_create_role("administrator" if is_admin else "user"))
    datastore.commit()
    return user


@ldap_blueprint.route("/api/ldap_login", methods=["POST"])
def ldap_login():
    body = request.get_json(silent=True) or {}
    # Flask-WTF reads the JSON body as the form data
    form = LDAPLoginForm()

    if not turnstile_passed(body.get("turnstile_token")):
        form.username.errors = list(form.username.errors or []) + [
            gettext("Please complete the human verification challenge")
        ]
        return base_render_json(form)

    if form.validate_on_submit():
        user = form.user
        login_user(user, remember=app.config.get("SECURITY_DEFAULT_REMEMBER_ME"), authn_via=["ldap"])
        payload = {"user": user.get_security_payload()}
        payload["user"]["authentication_token"] = user.get_auth_token()
        payload["csrf_token"] = generate_csrf()
        payload["identity_attributes"] = {"ldap": {}}
        return app.security._render_json(payload, 200, None, user)

    return base_render_json(form)
