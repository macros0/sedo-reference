"""Convert source timestamps to the shared representation for public views."""
from datetime import datetime, timedelta, timezone


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MICROSECOND = timedelta(microseconds=1)


def observed_datetime(value):
    try:
        stamp = datetime.fromisoformat(value)
        return stamp if stamp.utcoffset() is not None else None
    except (TypeError, ValueError):
        return None


def observation_clock(value):
    stamp = observed_datetime(value)
    if stamp is None:
        return dict(observed_at_us=None, observed_at_utc=None)
    try:
        utc = stamp.astimezone(timezone.utc)
    except OverflowError:
        return dict(observed_at_us=None, observed_at_utc=None)
    return dict(observed_at_us=str((utc - _EPOCH) // _MICROSECOND),
                observed_at_utc=utc.isoformat(timespec="microseconds").removesuffix("+00:00") + "Z")
