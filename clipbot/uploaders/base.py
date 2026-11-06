class UploadError(Exception):
    """Upload fehlgeschlagen; der Clip wird beim naechsten Lauf erneut versucht."""
