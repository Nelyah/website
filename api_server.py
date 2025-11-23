"""
Minimal upload API for GPX files.

- POST /upload with form-data:
    - file: GPX file (required)
    - title: section heading (required)
    - map_id: mapId for the shortcode (required)
- Auth: Bearer token in Authorization header. Set API_TOKEN env var.

Behavior:
- Stores the GPX into static/gpx/<slug>.gpx (adds suffix if the name exists).
- Appends a new leaflet map section to content/hikes-and-travels/index.md.
- Inserts the track into the "All my hikes" map using the ALL_HIKES_TRACKS placeholder
  and inserts the new section at the NEW_HIKE_SECTION placeholder (second slot).
- Runs git add/commit/push unless disabled via env:
    API_SKIP_GIT=true -> skip git add/commit/push
    API_SKIP_PUSH=true -> commit locally only

Run locally:
    pip install fastapi uvicorn python-multipart
    uvicorn api_server:app --host 0.0.0.0 --port 8000
"""

import asyncio
import os
import re
import fcntl
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional, Tuple
from contextlib import asynccontextmanager
import tempfile
import logging

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile

REPO_ROOT = Path(__file__).resolve().parent
STATIC_GPX_DIR = REPO_ROOT / "static" / "gpx"
INDEX_MD = REPO_ROOT / "content" / "hikes-and-travels" / "index.md"

API_TOKEN = os.environ.get("API_TOKEN")
# Lock file lives in tmp to avoid polluting the repo
LOCK_PATH = Path(tempfile.gettempdir()) / "hikes_upload.lock"
_async_lock = asyncio.Lock()  # intra-process guard to serialize within one worker
ALL_HIKES_PLACEHOLDER = "<!-- ALL_HIKES_TRACKS -->"
NEW_SECTION_PLACEHOLDER = "<!-- NEW_HIKE_SECTION -->"

logger = logging.getLogger("api")
# Init logging when the ASGI app starts (not via __main__), avoiding host logger overrides
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

app = FastAPI()


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or "track"


def ensure_unique_path(base_path: Path) -> Path:
    if not base_path.exists():
        return base_path
    stem = base_path.stem
    suffix = base_path.suffix
    counter = 1
    while True:
        candidate = base_path.with_name(f"{stem}-{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def extract_first_point(gpx_path: Path) -> Tuple[Optional[float], Optional[float]]:
    # Parse lazily to get the first trkpt without loading the whole file
    try:
        for _, elem in ET.iterparse(str(gpx_path)):
            if elem.tag.endswith("trkpt"):
                lat_attr = elem.attrib.get("lat")
                lon_attr = elem.attrib.get("lon")
                if lat_attr is None or lon_attr is None:
                    continue
                lat = float(lat_attr)
                lon = float(lon_attr)
                return lat, lon
    except Exception:
        return (None, None)
    return (None, None)


def append_map_entry(
    title: str,
    map_id: str,
    gpx_filename: str,
) -> str:
    return (
        f"\n## {title}\n\n"
        f'{{{{< leaflet-map mapHeight="70rem" mapWidth="100%" mapId="{map_id}" >}}}}\n'
        f'    {{{{< leaflet-track trackPath="{gpx_filename}" >}}}}\n'
        f"{{{{< /leaflet-map >}}}}\n"
    )


def add_track_at_placeholder(index_text: str, gpx_filename: str) -> str:
    track_snippet = f'    {{{{< leaflet-track trackPath="{gpx_filename}" showElevation=false showDownload=false >}}}}\n'
    if track_snippet in index_text:
        return index_text
    if ALL_HIKES_PLACEHOLDER not in index_text:
        raise HTTPException(
            status_code=500, detail="ALL_HIKES_TRACKS placeholder missing in index.md"
        )
    return index_text.replace(
        ALL_HIKES_PLACEHOLDER, ALL_HIKES_PLACEHOLDER + "\n" + track_snippet, 1
    )


def insert_section_at_placeholder(index_text: str, section_block: str) -> str:
    if NEW_SECTION_PLACEHOLDER not in index_text:
        raise HTTPException(
            status_code=500, detail="NEW_HIKE_SECTION placeholder missing in index.md"
        )
    return index_text.replace(
        NEW_SECTION_PLACEHOLDER, NEW_SECTION_PLACEHOLDER + "\n\n" + section_block, 1
    )


def update_index_with_new_track(
    title: str, map_identifier: str, gpx_filename: str
) -> None:
    index_text = INDEX_MD.read_text(encoding="utf-8")
    if f'mapId="{map_identifier}"' in index_text:
        raise HTTPException(status_code=400, detail="map_id already exists in index.md")
    updated = add_track_at_placeholder(index_text, gpx_filename)
    section_block = append_map_entry(title, map_identifier, gpx_filename)
    updated = insert_section_at_placeholder(updated, section_block)
    INDEX_MD.write_text(updated, encoding="utf-8")


def run_cmd(cmd: List[str], cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    logger.info("Running command: %s", " ".join(cmd))
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def maybe_git_commit_and_push(gpx_path: Path, message: str) -> Dict[str, str]:
    if os.environ.get("API_SKIP_GIT", "").lower() == "true":
        return {"git": "skipped"}
    try:
        run_cmd(["git", "add", str(gpx_path), str(INDEX_MD)])
        run_cmd(["git", "commit", "-m", message])
        if os.environ.get("API_SKIP_PUSH", "").lower() != "true":
            run_cmd(["git", "push"])
            return {"git": "pushed"}
        return {"git": "committed"}
    except subprocess.CalledProcessError as exc:
        err_out = (exc.stderr or "").strip()
        logger.error("Git command failed: %s | stdout=%s | stderr=%s", exc.cmd, exc.stdout, exc.stderr)
        if "user.name" in err_out or "user.email" in err_out:
            hint = "Configure git user.name/user.email or set API_SKIP_GIT=true"
            err_out = f"{err_out} ({hint})"
        raise HTTPException(status_code=500, detail=f"Git error: {err_out or exc}") from exc


def rebuild_site() -> Dict[str, str]:
    """Rebuild the Hugo site so the static output picks up new content."""
    try:
        run_cmd(
            [
                "hugo",
                "--environment",
                "production",
                "--minify",
                "--destination",
                str(REPO_ROOT / "public"),
            ]
        )
        return {"build": "success"}
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() if exc.stdout else ""
        raise HTTPException(status_code=500, detail=f"Hugo build failed: {detail}")


@asynccontextmanager
async def repo_lock() -> AsyncIterator[None]:
    """
    Async context manager combining an asyncio lock (intra-process) with an
    OS-level flock (inter-process). Ensures only one upload edits files at a time.
    """
    async with _async_lock:
        with open(LOCK_PATH, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


async def verify_token(authorization: str = Header(None)):
    if not API_TOKEN:
        raise HTTPException(status_code=500, detail="API_TOKEN not configured")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if token != API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/upload")
async def upload_gpx(
    file: UploadFile = File(...),
    title: str = Form(...),
    map_id: str = Form(...),
    _=Depends(verify_token),
):
    logger.info("Upload requested: filename=%s title=%s map_id=%s size=%s", file.filename, title, map_id, getattr(file, "size", None))
    filename = file.filename or "upload.gpx"
    if not filename.lower().endswith(".gpx"):
        raise HTTPException(status_code=400, detail="Only .gpx files are accepted")

    # Enforce size limit before reading into memory
    max_bytes = 25 * 1024 * 1024  # 25MB
    if file.size is not None and file.size > max_bytes:
        raise HTTPException(status_code=413, detail="File too large (max 25MB)")

    STATIC_GPX_DIR.mkdir(parents=True, exist_ok=True)

    base_name = slugify(title or Path(filename).stem)
    map_identifier = slugify(map_id) if map_id else base_name
    target_path = ensure_unique_path(STATIC_GPX_DIR / f"{base_name}.gpx")

    # Lock protects index.md and static/gpx writes across workers/processes
    async with repo_lock():
        contents = await file.read()
        if len(contents) > max_bytes:
            raise HTTPException(status_code=413, detail="File too large (max 25MB)")
        logger.info("Writing GPX to %s (%d bytes)", target_path, len(contents))
        with open(target_path, "wb") as out:
            out.write(contents)

        # Basic GPX validity: try parsing; fail fast if invalid XML/GPX
        try:
            for _event, elem in ET.iterparse(target_path, events=("start",)):
                if elem.tag.endswith("gpx"):
                    break
            else:
                raise ValueError("Not a GPX file")
        except Exception as exc:
            logger.error("Invalid GPX file: %s", exc)
            target_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"Invalid GPX file: {exc}") from exc

        update_index_with_new_track(
            title or base_name, map_identifier, target_path.name
        )

    center = extract_first_point(target_path)

    git_result = maybe_git_commit_and_push(target_path, f"Add GPX: {target_path.name}")

    build_result = rebuild_site()

    return {
        "status": "ok",
        "file": target_path.name,
        "map_id": map_identifier,
        "center": center,
        **git_result,
        **build_result,
    }
