from datetime import datetime

def parse_sqlite_datetime(date_val):
    """
    Safely parse datetime from SQLite which might return string or datetime objects.
    """
    if isinstance(date_val, str):
        try:
            return datetime.fromisoformat(date_val.replace('Z', '').split('.')[0])
        except Exception:
            return None
    return date_val
