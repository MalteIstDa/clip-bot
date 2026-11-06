"""Upload als YouTube Short ueber die offizielle YouTube Data API v3."""
from __future__ import annotations

from pathlib import Path

from .base import UploadError

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def authorize(client_secrets: Path, token_file: Path) -> None:
    """Einmalige Anmeldung im Browser; speichert den Token fuer spaetere Laeufe."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline",
                                  timeout_seconds=300)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding="utf-8")


def _credentials(token_file: Path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not token_file.exists():
        raise UploadError(f"{token_file} fehlt. Zuerst 'python auth.py youtube <kanal>' ausfuehren.")
    creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if not creds.valid:
        try:
            creds.refresh(Request())
        except Exception as exc:
            raise UploadError(f"YouTube-Token abgelaufen, bitte neu anmelden: {exc}") from exc
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds


def upload(video_path: Path, title: str, description: str, tags: list[str], token_file: Path,
           privacy: str = "public", category_id: str = "24") -> str:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    youtube = build("youtube", "v3", credentials=_credentials(token_file), cache_discovery=False)
    body = {
        "snippet": {"title": title[:100], "description": description[:4900],
                    "tags": tags, "categoryId": category_id},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    try:
        response = None
        while response is None:
            _, response = request.next_chunk(num_retries=3)
    except HttpError as exc:
        raise UploadError(f"YouTube: {exc}") from exc
    return response["id"]
