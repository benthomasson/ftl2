"""FTL Route 53 module.

Async DNS record management using aioboto3.
"""

from typing import Any

from ftl2.ftl_modules.exceptions import FTLModuleError, requires_extra

__all__ = ["ftl_route53_record", "ftl_route53_info"]


async def _resolve_zone_id(r53: Any, zone: str) -> str:
    """Resolve a zone name to a hosted zone ID."""
    if not zone.endswith("."):
        zone = zone + "."
    resp = await r53.list_hosted_zones_by_name(DNSName=zone, MaxItems="1")
    zones = resp.get("HostedZones", [])
    if not zones or zones[0]["Name"] != zone:
        raise FTLModuleError(
            f"Hosted zone not found: {zone}",
            zone=zone,
        )
    zone_id = zones[0]["Id"]
    return zone_id.split("/")[-1]


def _normalize_record_name(name: str) -> str:
    """Ensure record name ends with a dot (FQDN)."""
    if not name.endswith("."):
        return name + "."
    return name


@requires_extra("aws", "aioboto3")
async def ftl_route53_record(
    record: str,
    type: str = "A",
    value: str | list[str] | None = None,
    zone: str | None = None,
    hosted_zone_id: str | None = None,
    ttl: int = 300,
    state: str = "present",
    wait: bool = False,
    wait_timeout: int = 120,
    region: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Manage Route 53 DNS records.

    Args:
        record: DNS record name (e.g., "myhost.example.com")
        type: Record type (A, AAAA, CNAME, TXT, MX, etc.)
        value: Record value(s) — string or list for multiple values
        zone: Hosted zone name (e.g., "example.com") — resolved to zone ID
        hosted_zone_id: Hosted zone ID (skip lookup if provided)
        ttl: TTL in seconds (default 300)
        state: "present" (UPSERT) or "absent" (DELETE)
        wait: Wait for change propagation (default False)
        wait_timeout: Propagation timeout in seconds
        region: AWS region
        **kwargs: Additional parameters

    Returns:
        Result dict with changed, record, type, value, zone_id, change_id
    """
    import aioboto3
    from botocore.exceptions import BotoCoreError, ClientError

    state = state.lower()
    if state not in ("present", "absent"):
        raise FTLModuleError(
            f"Unsupported state: {state}",
            supported_states=["present", "absent"],
        )

    if state == "present" and value is None:
        raise FTLModuleError("value is required when state=present")

    if not zone and not hosted_zone_id:
        raise FTLModuleError("Either zone or hosted_zone_id is required")

    values = [value] if isinstance(value, str) else (value or [])
    record_name = _normalize_record_name(record)
    record_type = type.upper()

    session = aioboto3.Session()
    try:
        async with session.client("route53", region_name=region) as r53:
            zone_id = hosted_zone_id or await _resolve_zone_id(r53, zone)

            if state == "present":
                action = "UPSERT"
                resource_records = [{"Value": v} for v in values]
            else:
                action = "DELETE"
                existing = await _find_record(r53, zone_id, record_name, record_type)
                if not existing:
                    return {
                        "changed": False,
                        "record": record,
                        "type": record_type,
                        "value": values,
                        "zone_id": zone_id,
                    }
                resource_records = existing.get("ResourceRecords", [])
                ttl = existing.get("TTL", ttl)

            resp = await r53.change_resource_record_sets(
                HostedZoneId=zone_id,
                ChangeBatch={
                    "Changes": [{
                        "Action": action,
                        "ResourceRecordSet": {
                            "Name": record_name,
                            "Type": record_type,
                            "TTL": ttl,
                            "ResourceRecords": resource_records,
                        },
                    }],
                },
            )

            change_info = resp.get("ChangeInfo", {})
            change_id = change_info.get("Id", "").split("/")[-1]
            status = change_info.get("Status", "UNKNOWN")

            if wait and change_id:
                await _wait_for_change(r53, change_id, wait_timeout)
                status = "INSYNC"

            return {
                "changed": True,
                "record": record,
                "type": record_type,
                "value": values,
                "ttl": ttl,
                "zone_id": zone_id,
                "change_id": change_id,
                "status": status,
            }

    except FTLModuleError:
        raise
    except ClientError as e:
        raise FTLModuleError(
            f"AWS API error: {e}",
            record=record,
            zone=zone,
        ) from e
    except BotoCoreError as e:
        raise FTLModuleError(
            f"AWS connection error: {e}",
            record=record,
            zone=zone,
        ) from e
    except Exception as e:
        raise FTLModuleError(
            f"Route 53 operation failed: {e}",
            record=record,
            zone=zone,
        ) from e


async def _find_record(
    r53: Any, zone_id: str, record_name: str, record_type: str,
) -> dict[str, Any] | None:
    """Find an existing record set."""
    resp = await r53.list_resource_record_sets(
        HostedZoneId=zone_id,
        StartRecordName=record_name,
        StartRecordType=record_type,
        MaxItems="1",
    )
    for rr in resp.get("ResourceRecordSets", []):
        if rr["Name"] == record_name and rr["Type"] == record_type:
            return rr
    return None


async def _wait_for_change(r53: Any, change_id: str, timeout: int) -> None:
    """Wait for a Route 53 change to propagate."""
    import asyncio

    waited = 0
    interval = 5
    while waited < timeout:
        resp = await r53.get_change(Id=change_id)
        status = resp.get("ChangeInfo", {}).get("Status")
        if status == "INSYNC":
            return
        await asyncio.sleep(interval)
        waited += interval
    raise FTLModuleError(
        f"Timed out waiting for change {change_id} to propagate after {timeout}s",
        change_id=change_id,
    )


@requires_extra("aws", "aioboto3")
async def ftl_route53_info(
    zone: str | None = None,
    hosted_zone_id: str | None = None,
    record: str | None = None,
    type: str | None = None,
    region: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Query Route 53 DNS records.

    Args:
        zone: Hosted zone name (e.g., "example.com")
        hosted_zone_id: Hosted zone ID
        record: Optional — filter to specific record name
        type: Optional — filter to specific record type
        region: AWS region
        **kwargs: Additional parameters

    Returns:
        Result dict with records list
    """
    import aioboto3
    from botocore.exceptions import BotoCoreError, ClientError

    if not zone and not hosted_zone_id:
        raise FTLModuleError("Either zone or hosted_zone_id is required")

    session = aioboto3.Session()
    try:
        async with session.client("route53", region_name=region) as r53:
            zone_id = hosted_zone_id or await _resolve_zone_id(r53, zone)

            params: dict[str, Any] = {"HostedZoneId": zone_id}
            if record:
                params["StartRecordName"] = _normalize_record_name(record)
            if type:
                params["StartRecordType"] = type.upper()

            records = []
            while True:
                resp = await r53.list_resource_record_sets(**params)
                for rr in resp.get("ResourceRecordSets", []):
                    entry: dict[str, Any] = {
                        "name": rr["Name"].rstrip("."),
                        "type": rr["Type"],
                        "ttl": rr.get("TTL"),
                        "values": [r["Value"] for r in rr.get("ResourceRecords", [])],
                    }
                    if rr.get("AliasTarget"):
                        entry["alias"] = {
                            "dns_name": rr["AliasTarget"]["DNSName"],
                            "zone_id": rr["AliasTarget"]["HostedZoneId"],
                            "evaluate_health": rr["AliasTarget"].get(
                                "EvaluateTargetHealth", False
                            ),
                        }
                    if record and entry["name"] != record.rstrip("."):
                        continue
                    if type and entry["type"] != type.upper():
                        continue
                    records.append(entry)

                if not resp.get("IsTruncated"):
                    break
                params["StartRecordName"] = resp["NextRecordName"]
                params["StartRecordType"] = resp["NextRecordType"]

            return {
                "changed": False,
                "zone_id": zone_id,
                "records": records,
            }

    except FTLModuleError:
        raise
    except ClientError as e:
        raise FTLModuleError(
            f"AWS API error: {e}",
            zone=zone,
        ) from e
    except BotoCoreError as e:
        raise FTLModuleError(
            f"AWS connection error: {e}",
            zone=zone,
        ) from e
    except Exception as e:
        raise FTLModuleError(
            f"Route 53 query failed: {e}",
            zone=zone,
        ) from e
