#!/usr/bin/env python3
"""Einmalige Anmeldung der Upload-Konten:  python auth.py <youtube|tiktok|instagram> <kanalname>

Passwoerter gibst du dabei nur auf den offiziellen Seiten von Google/TikTok ein;
der Bot speichert lediglich die Zugriffstoken im Ordner tokens/.
"""
import sys

from clipbot import config
from clipbot.uploaders.base import UploadError


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in ("youtube", "tiktok", "instagram"):
        print(__doc__)
        return 1
    platform, name = sys.argv[1], sys.argv[2]
    try:
        cfg = config.load()
        channel = next((c for c in cfg["channels"] if c["name"] == name), None)
        if not channel or platform not in channel["upload"]:
            print(f"Kanal '{name}' hat in config.yaml keinen Eintrag upload.{platform}.")
            return 1
        target = channel["upload"][platform]
        if platform == "youtube":
            from clipbot.uploaders import youtube
            youtube.authorize(config.youtube_secrets_file(),
                              config.resolve(target["token_file"]))
        elif platform == "tiktok":
            from clipbot.uploaders import tiktok
            tiktok.authorize(config.env("TIKTOK_CLIENT_KEY"), config.env("TIKTOK_CLIENT_SECRET"),
                             config.env("TIKTOK_REDIRECT_URI"), config.resolve(target["token_file"]))
        else:
            from clipbot.uploaders import instagram
            user = instagram.whoami(str(target["ig_user_id"]), config.env(target["token_env"]),
                                    cfg["settings"]["instagram_api_version"])
            print(f"Token gueltig fuer Instagram-Konto @{user}")
    except (UploadError, config.ConfigError) as exc:
        print(f"Fehler: {exc}")
        return 1
    print(f"{platform} fuer '{name}' ist eingerichtet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
