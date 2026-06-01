"""Tiny key-value store for computed artifacts that should accrue over time.

Local filesystem by default (under ``.cache/store/``); S3 when
``PARKNOVA_S3_BUCKET`` is set and ``boto3`` is importable. Values are JSON.
Each ``save`` stamps an ``as_of`` ISO timestamp so callers can decide whether a
cached artifact is fresh enough (see :func:`age_seconds`).

Never raises on I/O problems — load returns None, save is best-effort — so a
storage hiccup degrades to "recompute" rather than crashing the app.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger("parknova.store")

LOCAL_ROOT = os.path.join(os.getcwd(), ".cache", "store")
S3_BUCKET = os.getenv("PARKNOVA_S3_BUCKET")
S3_PREFIX = os.getenv("PARKNOVA_S3_PREFIX", "parknova")


def _use_s3() -> bool:
    return bool(S3_BUCKET)


def _s3_client():
    try:
        import boto3
        return boto3.client("s3")
    except Exception as exc:  # boto3 missing / no creds -> fall back to local
        logger.warning("S3 requested but unavailable (%s); using local store.", exc)
        return None


def _local_path(key: str) -> str:
    return os.path.join(LOCAL_ROOT, key)


def save(key: str, payload: Any) -> bool:
    """Persist ``payload`` (JSON-serialisable) under ``key`` with an as_of stamp.

    Returns True on success. The stored object is ``{"as_of": iso, "data": ...}``.
    """
    envelope = {"as_of": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                "data": payload}
    body = json.dumps(envelope)
    if _use_s3():
        client = _s3_client()
        if client is not None:
            try:
                client.put_object(Bucket=S3_BUCKET,
                                  Key=f"{S3_PREFIX}/{key}",
                                  Body=body.encode("utf-8"),
                                  ContentType="application/json")
                return True
            except Exception as exc:
                logger.warning("S3 save failed for %s (%s); using local.", key, exc)
    try:
        path = _local_path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return True
    except OSError as exc:
        logger.warning("Local save failed for %s: %s", key, exc)
        return False


def _load_envelope(key: str) -> Optional[dict]:
    if _use_s3():
        client = _s3_client()
        if client is not None:
            try:
                obj = client.get_object(Bucket=S3_BUCKET, Key=f"{S3_PREFIX}/{key}")
                return json.loads(obj["Body"].read().decode("utf-8"))
            except Exception:
                return None  # not found / error -> treat as miss (recompute)
    path = _local_path(key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def load(key: str) -> Optional[Any]:
    """Return the stored ``data`` payload for ``key`` (None on miss/error)."""
    env = _load_envelope(key)
    return env.get("data") if isinstance(env, dict) else None


def age_seconds(key: str) -> Optional[float]:
    """Seconds since ``key`` was saved, or None if absent/unparseable."""
    env = _load_envelope(key)
    if not isinstance(env, dict) or "as_of" not in env:
        return None
    try:
        ts = _dt.datetime.fromisoformat(env["as_of"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_dt.timezone.utc)
        return (_dt.datetime.now(_dt.timezone.utc) - ts).total_seconds()
    except ValueError:
        return None


def as_of(key: str) -> Optional[str]:
    """ISO timestamp the artifact was saved, or None."""
    env = _load_envelope(key)
    return env.get("as_of") if isinstance(env, dict) else None


# ---------------------------------------------------------------------------
# Raw JSON interface (no envelope) — for records that carry their own metadata,
# e.g. thesis records under `.cache/theses/{ticker}.json`. Same local/S3 switch.
# ---------------------------------------------------------------------------
RAW_ROOT = os.path.join(os.getcwd(), ".cache")


def _raw_local_path(key: str) -> str:
    return os.path.join(RAW_ROOT, key)


def write_json(key: str, obj: Any) -> bool:
    """Write ``obj`` as raw JSON under ``key``. Best-effort; never raises."""
    body = json.dumps(obj, indent=2)
    if _use_s3():
        client = _s3_client()
        if client is not None:
            try:
                client.put_object(Bucket=S3_BUCKET, Key=f"{S3_PREFIX}/{key}",
                                  Body=body.encode("utf-8"),
                                  ContentType="application/json")
                return True
            except Exception as exc:
                logger.warning("S3 write_json failed for %s (%s); local.", key, exc)
    try:
        path = _raw_local_path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return True
    except OSError as exc:
        logger.warning("Local write_json failed for %s: %s", key, exc)
        return False


def read_json(key: str) -> Optional[Any]:
    """Read raw JSON under ``key`` (None on miss/error)."""
    if _use_s3():
        client = _s3_client()
        if client is not None:
            try:
                obj = client.get_object(Bucket=S3_BUCKET, Key=f"{S3_PREFIX}/{key}")
                return json.loads(obj["Body"].read().decode("utf-8"))
            except Exception:
                return None
    path = _raw_local_path(key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def list_keys(prefix: str) -> list:
    """List JSON keys under ``prefix`` (e.g. 'theses/'), relative to the root."""
    keys: list = []
    if _use_s3():
        client = _s3_client()
        if client is not None:
            try:
                token = None
                while True:
                    kw = {"Bucket": S3_BUCKET, "Prefix": f"{S3_PREFIX}/{prefix}"}
                    if token:
                        kw["ContinuationToken"] = token
                    resp = client.list_objects_v2(**kw)
                    for o in resp.get("Contents", []):
                        k = o["Key"]
                        if k.endswith(".json"):
                            keys.append(k[len(f"{S3_PREFIX}/"):])
                    if resp.get("IsTruncated"):
                        token = resp.get("NextContinuationToken")
                    else:
                        break
                return keys
            except Exception as exc:
                logger.warning("S3 list_keys failed for %s: %s", prefix, exc)
                return keys
    base = _raw_local_path(prefix)
    root_dir = base if os.path.isdir(base) else os.path.dirname(base)
    if not os.path.isdir(root_dir):
        return keys
    for name in sorted(os.listdir(root_dir)):
        if name.endswith(".json"):
            rel = os.path.relpath(os.path.join(root_dir, name), RAW_ROOT)
            keys.append(rel.replace(os.sep, "/"))
    return keys
