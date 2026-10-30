"""Lists the spaces the app was added to, marking which ones are allowed in the configuration.

Run: docker compose exec app python scripts/list_spaces.py
"""
import os

import google.auth
from google.auth.transport.requests import AuthorizedSession

credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/chat.bot"])
session = AuthorizedSession(credentials)
allowed = {s.strip() for s in os.environ.get("CLOUDOBSAI_ALLOWED_SPACES", "").split(",")}
page_token = None
while True:
    params = {"pageSize": 100, "filter": 'spaceType = "SPACE"'}
    if page_token:
        params["pageToken"] = page_token
    response = session.get("https://chat.googleapis.com/v1/spaces", params=params, timeout=30)
    response.raise_for_status()
    body = response.json()
    for space in body.get("spaces", []):
        status = "permitido" if space["name"] in allowed else "NAO permitido"
        print(f"{space['name']}\t{status}\t{space.get('displayName', '')}")
    page_token = body.get("nextPageToken")
    if not page_token:
        break
