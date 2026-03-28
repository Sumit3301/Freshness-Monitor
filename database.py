"""
SQLite Database Module
======================
Stores captured images (as BLOBs) and classification results in a local
SQLite database so they survive server restarts and redeployments.

Usage:
    import database
    database.init_db()
    database.save_prediction(result_dict, image_bytes, "image/jpeg")
    rows = database.get_predictions(limit=50)
    img_bytes, mime = database.get_image(prediction_id)
"""

import json
import sqlite3
import threading

import config

# One connection per thread (SQLite requirement).
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Return a per-thread SQLite connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(config.DB_PATH)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def init_db():
    """Create the predictions table if it doesn't exist."""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            filename        TEXT,
            timestamp       TEXT,
            stage           INTEGER,
            stage_name      TEXT,
            stage_color     TEXT,
            confidence      REAL,
            stage_probabilities TEXT,   -- JSON string
            hex_colors      TEXT,       -- JSON string
            image_data      BLOB,
            image_mimetype  TEXT
        )
    """)
    conn.commit()
    print(f"Database ready: {config.DB_PATH}")


def save_prediction(result: dict, image_bytes: bytes, mimetype: str = "image/jpeg"):
    """Insert a prediction row with the source image BLOB."""
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO predictions
            (filename, timestamp, stage, stage_name, stage_color,
             confidence, stage_probabilities, hex_colors,
             image_data, image_mimetype)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result.get("filename"),
            result.get("timestamp"),
            result.get("stage"),
            result.get("stage_name"),
            result.get("stage_color"),
            result.get("confidence"),
            json.dumps(result.get("stage_probabilities", {})),
            json.dumps(result.get("hex_colors", {})),
            image_bytes,
            mimetype,
        ),
    )
    conn.commit()


def get_predictions(limit: int = 50) -> list[dict]:
    """Return recent predictions (newest first), WITHOUT the image BLOB."""
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT id, filename, timestamp, stage, stage_name, stage_color,
               confidence, stage_probabilities, hex_colors
        FROM predictions
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    results = []
    for row in rows:
        results.append({
            "id": row["id"],
            "filename": row["filename"],
            "timestamp": row["timestamp"],
            "stage": row["stage"],
            "stage_name": row["stage_name"],
            "stage_color": row["stage_color"],
            "confidence": row["confidence"],
            "stage_probabilities": json.loads(row["stage_probabilities"]),
            "hex_colors": json.loads(row["hex_colors"]),
        })
    return results


def get_image(prediction_id: int) -> tuple[bytes, str] | None:
    """Return (image_bytes, mimetype) for a prediction, or None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT image_data, image_mimetype FROM predictions WHERE id = ?",
        (prediction_id,),
    ).fetchone()
    if row and row["image_data"]:
        return row["image_data"], row["image_mimetype"]
    return None
