import re
import csv
import io
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

import httpx
from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.saved_contact import SavedContact

logger = logging.getLogger(__name__)


def extract_sheet_id(url_or_id: str) -> Optional[str]:
    """Extract Google Spreadsheet ID from a link or bare ID."""
    if not url_or_id:
        return None
    url_or_id = url_or_id.strip()
    match = re.search(r"/d/([a-zA-Z0-9-_]{15,})", url_or_id)
    if match:
        return match.group(1)
    if len(url_or_id) >= 20 and "/" not in url_or_id:
        return url_or_id
    return None


def normalize_phone(phone: str) -> str:
    """Normalize phone number to standard E.164-like format."""
    p = "".join(c for c in str(phone) if c.isdigit() or c == "+").strip()
    if p.startswith("0") and len(p) == 11:
        p = f"+91{p[1:]}"
    elif not p.startswith("+"):
        if len(p) == 10:
            p = f"+91{p}"
        elif len(p) == 12 and p.startswith("91"):
            p = f"+{p}"
    return p


async def fetch_google_sheet_csv(sheet_id: str, gid: str = "0") -> str:
    """Fetch CSV content directly from public Google Sheet."""
    csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        res = await client.get(csv_url)
        if res.status_code != 200:
            raise ValueError(
                f"Google Sheet fetch failed with HTTP {res.status_code}. "
                "Please verify the sheet is shared with 'Anyone with the link can view'."
            )
        return res.text


def parse_csv_rows(csv_text: str) -> List[Dict[str, Any]]:
    """Parse CSV text into normalized contact dictionaries."""
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        return []

    # Map column headers
    field_keys = list(reader.fieldnames)
    name_col = next((f for f in field_keys if re.search(r"name|contact|person|full.*name", f, re.I)), None)
    phone_col = next((f for f in field_keys if re.search(r"phone|mobile|cell|num|tel|contact.*no", f, re.I)), None)
    email_col = next((f for f in field_keys if re.search(r"email|mail|e-mail", f, re.I)), None)

    contacts = []
    for idx, row in enumerate(reader):
        raw_name = (row.get(name_col) if name_col else "") or f"Contact {idx + 1}"
        raw_phone = (row.get(phone_col) if phone_col else "") or ""
        raw_email = (row.get(email_col) if email_col else "") or ""

        # Extract remaining fields as metadata
        metadata = {}
        for k, v in row.items():
            if k not in (name_col, phone_col, email_col) and v:
                metadata[str(k).strip()] = str(v).strip()

        contacts.append({
            "name": str(raw_name).strip(),
            "phone": str(raw_phone).strip(),
            "email": str(raw_email).strip() if raw_email else None,
            "metadata": metadata,
        })
    return contacts


async def sync_google_sheet_for_tag(
    db: AsyncSession,
    user_id: int,
    tag: str,
    sheet_url: str,
) -> Dict[str, Any]:
    """
    Sync Google Sheet rows into SavedContact for the given user and tag.
    - Adds newly added rows
    - Updates existing contact names, emails, and custom metadata
    - Deduplicates by normalized phone number
    """
    sheet_id = extract_sheet_id(sheet_url)
    if not sheet_id:
        raise ValueError(f"Invalid Google Sheet URL: {sheet_url}")

    # 1. Fetch CSV from Google
    csv_text = await fetch_google_sheet_csv(sheet_id)
    raw_contacts = parse_csv_rows(csv_text)
    if not raw_contacts:
        return {
            "success": True,
            "tag": tag,
            "added": 0,
            "updated": 0,
            "total_processed": 0,
            "message": "Google Sheet is empty or had no readable data rows."
        }

    # 2. Fetch existing contacts for this user & tag
    existing_res = await db.execute(
        select(SavedContact).where(
            SavedContact.user_id == user_id,
            SavedContact.tag == tag,
        )
    )
    existing_contacts = existing_res.scalars().all()
    existing_by_phone = {c.phone: c for c in existing_contacts}

    added_count = 0
    updated_count = 0
    now = datetime.now(timezone.utc)
    new_records = []
    seen_in_batch = set()

    for item in raw_contacts:
        norm_phone = normalize_phone(item["phone"])
        if not norm_phone or len(norm_phone) < 7:
            continue
        if norm_phone in seen_in_batch:
            continue
        seen_in_batch.add(norm_phone)

        name = item["name"] or "Unknown"
        email = item["email"]
        metadata = {
            **(item.get("metadata") or {}),
            "google_sheet_url": sheet_url,
            "google_sheet_id": sheet_id,
            "last_synced_at": now.isoformat(),
        }

        if norm_phone in existing_by_phone:
            # Update existing contact
            c = existing_by_phone[norm_phone]
            changed = False
            if name and c.name != name and name != "Unknown":
                c.name = name
                changed = True
            if email and c.email != email:
                c.email = email
                changed = True
            # Merge metadata
            c.metadata_fields = {**(c.metadata_fields or {}), **metadata}
            c.source = "Google Sheet"
            c.updated_at = now
            if changed:
                updated_count += 1
        else:
            # Add new contact
            new_c = SavedContact(
                user_id=user_id,
                name=name,
                phone=norm_phone,
                email=email,
                source="Google Sheet",
                tag=tag,
                metadata_fields=metadata,
                created_at=now,
                updated_at=now,
            )
            new_records.append(new_c)
            existing_by_phone[norm_phone] = new_c
            added_count += 1

    if new_records:
        db.add_all(new_records)

    await db.commit()

    return {
        "success": True,
        "tag": tag,
        "added": added_count,
        "updated": updated_count,
        "total_contacts": len(existing_by_phone),
        "last_synced_at": now.isoformat(),
    }
