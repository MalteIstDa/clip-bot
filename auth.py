#!/usr/bin/env python3
"""Einmalige Anmeldung eines Upload-Kontos ohne Dashboard:  python auth.py <konto>

<konto> ist die Kennung aus config.yaml (Abschnitt accounts, z. B. yt_meinkanal) oder der Name des Kontos.
Passwoerter gibst du dabei nur auf den offiziellen Seiten von Google/TikTok ein;
der Bot speichert lediglich die Zugriffstoken im Ordner tokens/.
"""
import sys

from clipbot import config
from clipbot.uploaders.base import UploadError


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    wanted = sys.argv[1]
    try:
        cfg = config.load(allow_empty=True)
        account = next((a for a in cfg["accounts"] if wanted in (a["id"], a.get("label"))), None)
        if not account:
            known = ", ".join(a["id"] for a in cfg["accounts"]) or "noch keine, lege sie im Dashboard unter 'Konten' an"
            print(f"Das Konto '{wanted}' gibt es nicht. Vorhandene Konten: {known}.")
            return 1
        platform = account["platform"]
        if platform == "youtube":
            from clipbot.uploaders import youtube
            youtube.authorize(config.youtube_secrets_file(), config.resolve(account["token_file"]))
        elif platform == "tiktok":
            from clipbot.uploaders import tiktok
            tiktok.authorize(config.env("TIKTOK_CLIENT_KEY"), config.env("TIKTOK_CLIENT_SECRET"),
                             config.env("TIKTOK_REDIRECT_URI"), config.resolve(account["token_file"]))
        else:
            from clipbot.uploaders import instagram
            user = instagram.whoami(str(account["ig_user_id"]), config.env(account["token_env"]),
                                    cfg["settings"]["instagram_api_version"])
            print(f"Token gueltig fuer Instagram-Konto @{user}")
    except (UploadError, config.ConfigError) as exc:
        print(f"Fehler: {exc}")
        return 1
    print(f"{platform}-Konto '{account.get('label') or account['id']}' ist eingerichtet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
