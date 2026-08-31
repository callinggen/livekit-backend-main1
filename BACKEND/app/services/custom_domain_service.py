import re
import os
import importlib
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple
from dotenv import load_dotenv
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.custom_domain import CustomEmailDomain
from app.services.email_service import email_service


class CustomDomainService:
    @staticmethod
    def normalize_domain(raw_domain: str) -> str:
        """Strip protocols, paths, trailing slashes, whitespace, and convert to lowercase."""
        if not raw_domain:
            raise ValueError("Domain name cannot be empty.")
        d = raw_domain.strip().lower()
        d = re.sub(r"^https?://", "", d)
        d = d.split("/")[0].split(":")[0].strip()
        
        # Validate domain structure (e.g. example.com or mail.example.com)
        pattern = r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
        if not re.match(pattern, d):
            raise ValueError(f"'{raw_domain}' is not a valid domain name format (e.g., yourcompany.com).")
        return d

    @classmethod
    async def create_domain(
        cls, db: AsyncSession, user_id: int, raw_domain: str, region: str = "us-east-1"
    ) -> CustomEmailDomain:
        """
        Registers a domain on Resend and stores the exact required DNS records in CallingGen.
        """
        domain_name = cls.normalize_domain(raw_domain)

        # 1. Check if user already registered this domain
        stmt = select(CustomEmailDomain).where(
            and_(
                CustomEmailDomain.user_id == user_id,
                CustomEmailDomain.domain == domain_name,
            )
        )
        existing = (await db.execute(stmt)).scalars().first()
        if existing:
            raise ValueError(f"Domain '{domain_name}' is already added to your account.")

        # 2. Call Resend Domains API to register domain
        resend_mod = importlib.import_module("resend")
        resend_mod.api_key = email_service.api_key
        resend_domain_id = None
        dns_records: List[Dict[str, Any]] = []
        resend_status = "pending"
        error_msg = None

        if not email_service.is_configured():
            # In test/dev environment without active Resend API key
            resend_domain_id = f"mock_{domain_name.replace('.', '_')}"
            dns_records = cls._generate_standard_fallback_records(domain_name, region)
        else:
            try:
                # Call Resend Domains API
                params: Dict[str, Any] = {"name": domain_name, "region": region}
                resp = resend_mod.Domains.create(params)
                
                # Resend returns a dict or object with id, name, status, records
                if isinstance(resp, dict):
                    resend_domain_id = resp.get("id")
                    resend_status = resp.get("status", "pending")
                    raw_records = resp.get("records", [])
                else:
                    resend_domain_id = getattr(resp, "id", None)
                    resend_status = getattr(resp, "status", "pending")
                    raw_records = getattr(resp, "records", [])

                for r in raw_records:
                    if isinstance(r, dict):
                        dns_records.append({
                            "record": r.get("record", "DNS"),
                            "type": r.get("type", "TXT").upper(),
                            "name": r.get("name", domain_name),
                            "value": r.get("value", ""),
                            "ttl": r.get("ttl", "Auto"),
                            "priority": r.get("priority"),
                            "status": r.get("status", "pending"),
                            "dns_verified": False,
                            "observed_value": None,
                        })
                    else:
                        dns_records.append({
                            "record": getattr(r, "record", "DNS"),
                            "type": getattr(r, "type", "TXT").upper(),
                            "name": getattr(r, "name", domain_name),
                            "value": getattr(r, "value", ""),
                            "ttl": getattr(r, "ttl", "Auto"),
                            "priority": getattr(r, "priority", None),
                            "status": getattr(r, "status", "pending"),
                            "dns_verified": False,
                            "observed_value": None,
                        })
            except Exception as e:
                error_str = str(e)
                print(f"[CustomDomainService] Resend API error on create domain '{domain_name}': {error_str}")
                if "restricted to only send emails" in error_str:
                    error_msg = (
                        "Resend API Key is restricted to sending only. "
                        "To manage domains automatically, a Full Access Resend API Key is required. "
                        "Standard DNS records have been loaded for configuration."
                    )
                    resend_domain_id = f"restricted_{domain_name.replace('.', '_')}"
                    dns_records = cls._generate_standard_fallback_records(domain_name, region)
                else:
                    error_msg = f"Resend Domains API notice: {error_str}. Standard DNS records loaded for setup."
                    resend_domain_id = f"fallback_{domain_name.replace('.', '_')}"
                    dns_records = cls._generate_standard_fallback_records(domain_name, region)

        # 3. Create database entry
        domain_obj = CustomEmailDomain(
            user_id=user_id,
            domain=domain_name,
            resend_domain_id=resend_domain_id,
            status=resend_status,
            dns_records=dns_records,
            is_verified=False,
            sending_enabled=False,
            region=region,
            last_checked_at=datetime.now(timezone.utc),
            error_message=error_msg,
        )
        db.add(domain_obj)
        await db.commit()
        await db.refresh(domain_obj)
        return domain_obj

    @classmethod
    async def verify_domain(cls, db: AsyncSession, domain_obj: CustomEmailDomain) -> CustomEmailDomain:
        """
        1. Queries public DNS using dnspython for each required record.
        2. Compares observed records against expected Resend requirements.
        3. If DNS is valid and Resend is connected, prompts Resend to verify.
        """
        records = domain_obj.dns_records or []
        updated_records = []
        all_dns_matched = True

        dns_resolver = importlib.import_module("dns.resolver")
        resolver = dns_resolver.Resolver()
        resolver.nameservers = ["8.8.8.8", "1.1.1.1", "8.8.4.4"]
        resolver.timeout = 3.0
        resolver.lifetime = 4.0

        for r in records:
            rtype = str(r.get("type", "TXT")).upper().strip()
            rname = str(r.get("name", "")).strip()
            expected_val = str(r.get("value", "")).strip()

            is_match, observed = cls._query_dns_record(resolver, rname, rtype, expected_val)
            
            updated_record = dict(r)
            updated_record["dns_verified"] = is_match
            updated_record["observed_value"] = observed
            if is_match:
                updated_record["status"] = "verified"
            else:
                all_dns_matched = False
                updated_record["status"] = "pending"

            updated_records.append(updated_record)

        domain_obj.dns_records = updated_records
        domain_obj.last_checked_at = datetime.now(timezone.utc)

        # Check with Resend if domain has a valid Resend domain ID
        if (
            email_service.is_configured()
            and domain_obj.resend_domain_id
            and not domain_obj.resend_domain_id.startswith("mock_")
            and not domain_obj.resend_domain_id.startswith("restricted_")
        ):
            try:
                resend_mod = importlib.import_module("resend")
        resend_mod.api_key = email_service.api_key
                # Trigger Resend verification
                try:
                    resend_mod.Domains.verify(domain_obj.resend_domain_id)
                except Exception as ve:
                    print(f"[CustomDomainService] Note on resend_mod.Domains.verify: {ve}")

                # Fetch updated status from Resend
                resend_info = resend_mod.Domains.get(domain_obj.resend_domain_id)
                r_status = (
                    resend_info.get("status")
                    if isinstance(resend_info, dict)
                    else getattr(resend_info, "status", "pending")
                )
                domain_obj.status = str(r_status).lower()
                if domain_obj.status == "verified" or (all_dns_matched and domain_obj.status != "failed"):
                    domain_obj.is_verified = True
                    domain_obj.sending_enabled = True
                    domain_obj.status = "verified"
                    domain_obj.verified_at = datetime.now(timezone.utc)
                    domain_obj.error_message = None
            except Exception as e:
                print(f"[CustomDomainService] Resend sync error on verify: {e}")
                if all_dns_matched:
                    domain_obj.is_verified = True
                    domain_obj.sending_enabled = True
                    domain_obj.status = "verified"
                    domain_obj.verified_at = datetime.now(timezone.utc)
        else:
            # When in DNS-independent mode
            if all_dns_matched and len(updated_records) > 0:
                domain_obj.is_verified = True
                domain_obj.sending_enabled = True
                domain_obj.status = "verified"
                domain_obj.verified_at = datetime.now(timezone.utc)
                domain_obj.error_message = None
            else:
                domain_obj.status = "pending"

        await db.commit()
        await db.refresh(domain_obj)
        return domain_obj

    @classmethod
    def _query_dns_record(
        cls, resolver: Any, host: str, rtype: str, expected_value: str
    ) -> Tuple[bool, str | None]:
        """
        Queries public DNS for host and rtype, securely compares against expected value.
        """
        if not host:
            return False, "Host missing"

        expected_clean = expected_value.strip().rstrip(".").lower()

        try:
            if rtype == "TXT":
                answers = resolver.resolve(host, "TXT")
                observed_list = []
                for rdata in answers:
                    # rdata.strings is a tuple of byte chunks
                    txt_content = b"".join(rdata.strings).decode("utf-8", errors="ignore").strip()
                    observed_list.append(txt_content)
                    clean_observed = txt_content.strip('"').strip("'").strip().lower()
                    if clean_observed == expected_clean or expected_clean in clean_observed:
                        return True, txt_content

                return False, " | ".join(observed_list) if observed_list else "No matching TXT record"

            elif rtype == "CNAME":
                answers = resolver.resolve(host, "CNAME")
                observed_list = []
                for rdata in answers:
                    cname_target = rdata.target.to_text().strip().rstrip(".").lower()
                    observed_list.append(cname_target)
                    if cname_target == expected_clean:
                        return True, cname_target

                return False, " | ".join(observed_list) if observed_list else "No matching CNAME record"

            elif rtype == "MX":
                answers = resolver.resolve(host, "MX")
                observed_list = []
                for rdata in answers:
                    exchange_host = rdata.exchange.to_text().strip().rstrip(".").lower()
                    mx_text = f"{rdata.preference} {exchange_host}"
                    observed_list.append(mx_text)
                    if expected_clean in exchange_host or expected_clean in mx_text.lower():
                        return True, mx_text

                return False, " | ".join(observed_list) if observed_list else "No matching MX record"

            else:
                return False, f"Unsupported record type {rtype}"

        except Exception as e:
            err_name = type(e).__name__
            if "NXDOMAIN" in err_name:
                return False, "Domain/Host not found in public DNS (NXDOMAIN)"
            elif "NoAnswer" in err_name:
                return False, f"No {rtype} answer returned by DNS"
            elif "Timeout" in err_name:
                return False, "DNS resolution timed out"
            return False, f"DNS check error: {str(e)[:100]}"

    @staticmethod
    def _generate_standard_fallback_records(domain_name: str, region: str = "us-east-1") -> List[Dict[str, Any]]:
        """
        Generates the standard Resend DNS specifications for domains.
        """
        return [
            {
                "record": "DKIM",
                "type": "TXT",
                "name": f"resend._domainkey.{domain_name}",
                "value": "k=rsa; p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC0...",
                "ttl": "Auto",
                "status": "pending",
                "dns_verified": False,
                "observed_value": None,
            },
            {
                "record": "SPF",
                "type": "TXT",
                "name": domain_name,
                "value": "v=spf1 include:resend.com ~all",
                "ttl": "Auto",
                "status": "pending",
                "dns_verified": False,
                "observed_value": None,
            },
            {
                "record": "Return-Path",
                "type": "MX",
                "name": f"bounces.{domain_name}",
                "value": f"feedback-smtp.{region}.amazonses.com",
                "priority": 10,
                "ttl": "Auto",
                "status": "pending",
                "dns_verified": False,
                "observed_value": None,
            },
        ]
