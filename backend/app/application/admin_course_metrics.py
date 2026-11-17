"""Course metrics stay separate from plan purchase signals and Hotmart sales."""
from hashlib import sha256

SOURCES = ("conversao", "pagina", "email")


def _course_cte(service, *, start_at, end_at, identity_type):
    stamp = "e.created_at::timestamptz" if service._use_postgres else "julianday(e.created_at)"
    identity_filter = "" if identity_type == "all" else " AND e.identity_type = ?"
    # URL query strings contain '?' too: bind them as values before the DB adapter
    # translates qmark placeholders for PostgreSQL.
    paths = tuple(f"/curso-notas-explicativas.html?from={source}" for source in ("conversao", "email", "pagina"))
    params = (*paths, start_at, end_at) if identity_type == "all" else (*paths, start_at, end_at, identity_type)
    return f"""WITH course_events AS (
        SELECT e.*, {stamp} AS stamp, CASE
          WHEN e.event_type IN ('course_cta_view', 'course_cta_click') AND e.page_path IN
            ('/ofx-convert.html', '/converter-pdf-para-ofx.html', '/converter-pdf-para-excel.html') THEN 'conversao'
          WHEN e.page_path = ? THEN 'conversao'
          WHEN e.page_path = ? THEN 'email'
          WHEN e.page_path IN ('/curso-notas-explicativas.html', ?) THEN 'pagina'
          ELSE NULL END AS source
        FROM product_events e
        WHERE e.created_at >= ? AND e.created_at <= ? {identity_filter}
          AND e.identity_id <> '' AND e.identity_type IN ('registered', 'anonymous')
          AND e.event_type IN ('course_cta_view', 'course_cta_click', 'course_page_view', 'course_outbound_click')
          AND NOT EXISTS (SELECT 1 FROM users u WHERE e.identity_type='registered' AND u.id=e.identity_id AND u.is_admin)
    ) """, params


def course_funnel(service, conn, *, start_at, end_at, identity_type):
    cte, params = _course_cte(service, start_at=start_at, end_at=end_at, identity_type=identity_type)
    rows = service._fetchall(conn, cte + """, people AS (
        SELECT source, identity_type, identity_id,
          MIN(CASE WHEN event_type='course_cta_view' THEN stamp END) AS first_exposure,
          MAX(CASE WHEN event_type='course_cta_click' THEN stamp END) AS last_cta_click,
          MIN(CASE WHEN event_type='course_page_view' THEN stamp END) AS first_visit,
          MAX(CASE WHEN event_type='course_outbound_click' THEN stamp END) AS last_outbound
        FROM course_events WHERE source IS NOT NULL GROUP BY source, identity_type, identity_id
    ) SELECT source,
        SUM(CASE WHEN first_exposure IS NOT NULL THEN 1 ELSE 0 END) AS cta_view_people,
        SUM(CASE WHEN last_cta_click >= first_exposure THEN 1 ELSE 0 END) AS cta_click_people,
        SUM(CASE WHEN first_visit IS NOT NULL THEN 1 ELSE 0 END) AS page_view_people,
        SUM(CASE WHEN last_outbound >= first_visit THEN 1 ELSE 0 END) AS outbound_click_people
      FROM people GROUP BY source""", params)
    indexed = {row["source"]: dict(row) for row in rows}
    result = []
    for source in SOURCES:
        item = {"source": source, **{key: int(indexed.get(source, {}).get(key) or 0) for key in (
            "cta_view_people", "cta_click_people", "page_view_people", "outbound_click_people")}}
        item["cta_ctr"] = _rate(item["cta_click_people"], item["cta_view_people"])
        item["page_ctr"] = _rate(item["outbound_click_people"], item["page_view_people"])
        result.append(item)
    return result


def _rate(numerator, denominator):
    return round(100 * numerator / denominator, 2) if denominator else None


def course_people(service, conn, *, start_at, end_at, identity_type):
    cte, params = _course_cte(service, start_at=start_at, end_at=end_at, identity_type=identity_type)
    rows = service._fetchall(conn, cte + """SELECT identity_type, identity_id,
        SUM(CASE WHEN event_type='course_page_view' THEN 1 ELSE 0 END) AS course_page_views,
        SUM(CASE WHEN event_type='course_outbound_click' THEN 1 ELSE 0 END) AS course_outbound_clicks,
        MAX(CASE WHEN event_type='course_page_view' THEN created_at END) AS last_course_page_view_at,
        MAX(CASE WHEN event_type='course_outbound_click' THEN created_at END) AS last_course_outbound_click_at
      FROM course_events WHERE source IS NOT NULL
        AND event_type IN ('course_page_view', 'course_outbound_click')
      GROUP BY identity_type, identity_id""", params)
    return {f"{row['identity_type']}:{row['identity_id']}": dict(row) for row in rows}


def attach_course_interest(items, people, profiles, *, include_visitors):
    existing = {item["raw_identity_key"] for item in items}
    if include_visitors:
        for key, person in people.items():
            if key in existing:
                continue
            kind, owner = person["identity_type"], person["identity_id"]
            reference = owner if kind == "registered" else f"anon_{sha256(owner.encode()).hexdigest()[:12]}"
            profile = profiles.get(owner, {}) if kind == "registered" else {}
            items.append({
                "raw_identity_key": key, "identity_type": kind, "identity_reference": reference,
                "display_name": profile.get("name") or ("Pessoa cadastrada" if kind == "registered" else f"Anônima {reference[-6:]}"),
                "email": profile.get("email"), "conversions": 0, "pages": 0, "successes": 0, "review": 0,
                "failures": 0, "transactions": 0, "active_days": 0, "is_returning": False,
                "last_activity_at": None, "top_models": [],
            })
    for item in items:
        person = people.get(item["raw_identity_key"], {})
        for field in ("course_page_views", "course_outbound_clicks"):
            item[field] = int(person.get(field) or 0)
        for field in ("last_course_page_view_at", "last_course_outbound_click_at"):
            item[field] = person.get(field)
    return items
