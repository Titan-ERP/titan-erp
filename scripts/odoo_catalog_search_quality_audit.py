"""Read-only live audit for storefront catalog and search quality."""

from __future__ import annotations

from odoo_cleanup_published_placeholders import connect, execute


def count(models, db, uid, key, domain):
    return execute(
        models,
        db,
        uid,
        key,
        "product.template",
        "search_count",
        [domain],
    )


def main() -> int:
    db, uid, key, models = connect()
    fields = execute(
        models,
        db,
        uid,
        key,
        "product.template",
        "fields_get",
        [],
        {"attributes": ["readonly"]},
    )
    published_field = (
        "website_published" if "website_published" in fields else "is_published"
    )
    published = [("active", "=", True), (published_field, "=", True)]

    internal_misc_ids = execute(
        models,
        db,
        uid,
        key,
        "product.category",
        "search",
        [[("complete_name", "ilike", "Miscellaneous")]],
        {"limit": 0},
    )
    website_misc_ids = execute(
        models,
        db,
        uid,
        key,
        "product.public.category",
        "search",
        [[("name", "ilike", "Misc")]],
        {"limit": 0},
    )

    internal_misc = []
    for category_id in internal_misc_ids:
        category = execute(
            models,
            db,
            uid,
            key,
            "product.category",
            "read",
            [[category_id]],
            {"fields": ["complete_name"]},
        )[0]
        internal_misc.append(
            {
                "id": category_id,
                "category": category["complete_name"],
                "active_products": count(
                    models,
                    db,
                    uid,
                    key,
                    [
                        ("active", "=", True),
                        ("categ_id", "=", category_id),
                    ],
                ),
            }
        )
    website_misc = []
    for category_id in website_misc_ids:
        category = execute(
            models,
            db,
            uid,
            key,
            "product.public.category",
            "read",
            [[category_id]],
            {"fields": ["name", "parent_id"]},
        )[0]
        website_misc.append(
            {
                "id": category_id,
                "category": category["name"],
                "active_products": count(
                    models,
                    db,
                    uid,
                    key,
                    [
                        ("active", "=", True),
                        ("public_categ_ids", "in", [category_id]),
                    ],
                ),
            }
        )

    stats = {
        "published": count(models, db, uid, key, published),
        "published_missing_category": count(
            models,
            db,
            uid,
            key,
            published + [("public_categ_ids", "=", False)],
        ),
        "published_placeholder_price": count(
            models,
            db,
            uid,
            key,
            published + [("list_price", "<=", 1.0)],
        ),
        "active_internal_misc": (
            count(
                models,
                db,
                uid,
                key,
                [("active", "=", True), ("categ_id", "in", internal_misc_ids)],
            )
            if internal_misc_ids
            else 0
        ),
        "active_website_misc": (
            count(
                models,
                db,
                uid,
                key,
                [("active", "=", True), ("public_categ_ids", "in", website_misc_ids)],
            )
            if website_misc_ids
            else 0
        ),
        "oem_bearing_names": count(
            models,
            db,
            uid,
            key,
            [("active", "=", True), ("name", "ilike", "OEM ")],
        ),
    }
    print(stats)
    print({"internal_misc_categories": internal_misc})
    print({"website_misc_categories": website_misc})
    service_categories = execute(
        models,
        db,
        uid,
        key,
        "product.category",
        "search_read",
        [[("complete_name", "ilike", "Service")]],
        {"fields": ["id", "complete_name"], "limit": 100},
    )
    print({"service_categories": service_categories})
    return int(
        any(
            stats[name]
            for name in (
                "published_missing_category",
                "published_placeholder_price",
                "active_internal_misc",
                "active_website_misc",
            )
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())

