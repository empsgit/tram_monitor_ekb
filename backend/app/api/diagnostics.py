"""Diagnostics API for verifying route-stop data pipeline."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])

# Will be set by main.py
tracker = None


@router.get("")
async def get_diagnostics():
    """Get full pipeline diagnostics: route-stop resolution, geometry, vehicle matching."""
    if tracker is None:
        return {"error": "Tracker not initialized"}
    return tracker.get_diagnostics()


@router.get("/routes/{route_id}")
async def get_route_diagnostics(route_id: int):
    """Get diagnostics for a specific route."""
    if tracker is None:
        return {"error": "Tracker not initialized"}
    diag = tracker.get_diagnostics()
    for r in diag["routes"]:
        if r["route_id"] == route_id:
            return r
    return {"error": "Route not found"}


@router.get("/ettu-routes-raw")
async def get_raw_ettu_routes():
    """Fetch raw ETTU routes response for debugging stop parsing."""
    if tracker is None:
        return {"error": "Tracker not initialized"}
    import httpx
    from app.config import settings
    try:
        async with httpx.AsyncClient(
            base_url=settings.ettu_base_url, timeout=30.0,
            params={"apiKey": "111"}, verify=False,
        ) as client:
            resp = await client.get("/api/v2/tram/routes/")
            data = resp.json()
            items = data if isinstance(data, list) else data.get("routes", [])
            # Return condensed view: route number + element keys/structure
            result = []
            for item in items:
                num = item.get("num", item.get("NUM", item.get("number", "?")))
                elements = item.get("elements", [])
                elem_info = []
                if isinstance(elements, list):
                    for elem in elements:
                        fp = elem.get("full_path")
                        p = elem.get("path")
                        st = elem.get("stops", elem.get("stations"))
                        sample_fp = fp[:2] if isinstance(fp, list) and fp else fp
                        sample_p = p[:2] if isinstance(p, list) and p else p
                        sample_st = st[:2] if isinstance(st, list) and st else st
                        elem_info.append({
                            "keys": list(elem.keys()),
                            "ind": elem.get("ind"),
                            "full_path_type": type(fp).__name__ if fp is not None else "missing",
                            "full_path_len": len(fp) if isinstance(fp, list) else None,
                            "full_path_sample": sample_fp,
                            "path_type": type(p).__name__ if p is not None else "missing",
                            "path_len": len(p) if isinstance(p, list) else None,
                            "path_sample": sample_p,
                            "stops_sample": sample_st,
                        })
                result.append({
                    "num": num,
                    "id": item.get("id", item.get("ID")),
                    "top_keys": list(item.keys()),
                    "elements_count": len(elements) if isinstance(elements, list) else type(elements).__name__,
                    "elements": elem_info,
                    "has_route_stops": "stops" in item or "stations" in item,
                })
            return {"routes": result}
    except Exception as e:
        return {"error": str(e)}


@router.get("/projection")
async def get_projection_diagnostics(limit: int = 100):
    """Get recent projection anomalies (out-of-section/backward/far-snap)."""
    if tracker is None:
        return {"error": "Tracker not initialized"}
    return tracker.get_projection_diagnostics(limit=limit)
