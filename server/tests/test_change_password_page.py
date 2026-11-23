"""The forced Change Password page tells users the minimum password length."""

import time

from flask_security import hash_password

from raven.extensions import db


def test_change_password_page_states_minimum_length(app, client):
    with app.app_context():
        ds = app.security.datastore
        ds.find_or_create_role("user")
        user = ds.find_user(username="pwhint") or ds.create_user(
            username="pwhint", password=hash_password("pwhint-pass"), roles=["user"]
        )
        db.session.commit()
        uniquifier = user.fs_uniquifier
    with client.session_transaction() as session:
        session["_user_id"] = uniquifier
        session["_fresh"] = True
        session["fs_cc"] = "set"
        session["fs_paa"] = time.time()

    response = client.get("/api/password/change", headers={"Accept": "text/html"})
    assert response.status_code == 200, response.status_code
    html = response.get_data(as_text=True)
    minimum = app.config["SECURITY_PASSWORD_LENGTH_MIN"]
    assert minimum == 9
    assert f"at least {minimum} characters long" in html
    assert f'minlength="{minimum}"' in html
