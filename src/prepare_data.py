#!/usr/bin/env python3
"""Build compact Parquet analytics marts from the Instacart CSV files."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import duckdb


FILE_ALIASES = {
    "orders": {"orders"},
    "prior": {"orderproductsprior", "orderproductprior"},
    "train": {"orderproductstrain", "orderproducttrain"},
    "products": {"products"},
    "aisles": {"aisle", "aisles"},
    "departments": {"department", "departments"},
}


def normalize_filename(filename: str) -> str:
    return re.sub(r"[^a-z0-9]", "", Path(filename).stem.lower())


def find_sources(data_dir: Path) -> dict[str, Path]:
    if not data_dir.is_dir():
        raise FileNotFoundError(f"Data directory does not exist: {data_dir}")

    csv_files = list(data_dir.glob("*.csv"))
    sources: dict[str, Path] = {}
    for key, aliases in FILE_ALIASES.items():
        matches = [path for path in csv_files if normalize_filename(path.name) in aliases]
        if len(matches) != 1:
            candidates = ", ".join(path.name for path in matches) or "none"
            raise ValueError(
                f"Expected exactly one CSV for '{key}' in {data_dir}; found {candidates}."
            )
        sources[key] = matches[0]
    return sources


def sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def csv_relation(path: Path) -> str:
    return f"read_csv_auto({sql_string(path.resolve())}, header = true, all_varchar = true)"


def parquet_relation(path: Path) -> str:
    return f"read_parquet({sql_string(path.resolve())})"


def copy_parquet(connection: duckdb.DuckDBPyConnection, query: str, path: Path) -> None:
    connection.execute(
        f"COPY ({query}) TO {sql_string(path.resolve())} "
        "(FORMAT PARQUET, COMPRESSION ZSTD)"
    )


def prepare_data(data_dir: Path, output_dir: Path, force: bool = False) -> dict[str, int]:
    sources = find_sources(data_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths = {
        name: output_dir / f"{name}.parquet"
        for name in (
            "orders",
            "order_products",
            "catalog",
            "order_mart",
            "customer_mart",
            "product_mart",
            "department_mart",
            "aisle_mart",
            "hourly_mart",
        )
    }
    metadata_path = output_dir / "metadata.json"
    existing = [path for path in [*output_paths.values(), metadata_path] if path.exists()]
    if existing and not force:
        raise FileExistsError(
            f"Processed files already exist in {output_dir}. Pass --force to replace them."
        )
    if force:
        for path in existing:
            path.unlink()

    connection = duckdb.connect()
    try:
        orders_query = f"""
            SELECT
                CAST(order_id AS BIGINT) AS order_id,
                CAST(user_id AS BIGINT) AS user_id,
                CAST(eval_set AS VARCHAR) AS eval_set,
                CAST(order_number AS INTEGER) AS order_number,
                CAST(order_dow AS INTEGER) AS order_dow,
                CAST(order_hour_of_day AS INTEGER) AS order_hour_of_day,
                TRY_CAST(days_since_prior_order AS DOUBLE) AS days_since_prior_order
            FROM {csv_relation(sources["orders"])}
        """
        copy_parquet(connection, orders_query, output_paths["orders"])

        order_products_query = f"""
            SELECT
                CAST(order_id AS BIGINT) AS order_id,
                CAST(product_id AS BIGINT) AS product_id,
                CAST(add_to_cart_order AS INTEGER) AS add_to_cart_order,
                CAST(reordered AS INTEGER) AS reordered,
                'prior' AS source_set
            FROM {csv_relation(sources["prior"])}
            UNION ALL
            SELECT
                CAST(order_id AS BIGINT) AS order_id,
                CAST(product_id AS BIGINT) AS product_id,
                CAST(add_to_cart_order AS INTEGER) AS add_to_cart_order,
                CAST(reordered AS INTEGER) AS reordered,
                'train' AS source_set
            FROM {csv_relation(sources["train"])}
        """
        copy_parquet(connection, order_products_query, output_paths["order_products"])

        catalog_query = f"""
            SELECT
                CAST(p.product_id AS BIGINT) AS product_id,
                CAST(p.product_name AS VARCHAR) AS product_name,
                CAST(p.aisle_id AS BIGINT) AS aisle_id,
                CAST(a.aisle AS VARCHAR) AS aisle_name,
                CAST(p.department_id AS BIGINT) AS department_id,
                CAST(d.department AS VARCHAR) AS department_name
            FROM {csv_relation(sources["products"])} p
            LEFT JOIN {csv_relation(sources["aisles"])} a
                ON CAST(p.aisle_id AS BIGINT) = CAST(a.aisle_id AS BIGINT)
            LEFT JOIN {csv_relation(sources["departments"])} d
                ON CAST(p.department_id AS BIGINT) = CAST(d.department_id AS BIGINT)
        """
        copy_parquet(connection, catalog_query, output_paths["catalog"])

        orders = parquet_relation(output_paths["orders"])
        order_products = parquet_relation(output_paths["order_products"])
        catalog = parquet_relation(output_paths["catalog"])

        order_mart_query = f"""
            WITH line_totals AS (
                SELECT
                    order_id,
                    COUNT(*) AS item_count,
                    SUM(reordered) AS reordered_items,
                    AVG(add_to_cart_order) AS avg_cart_position
                FROM {order_products}
                GROUP BY order_id
            )
            SELECT
                o.order_id,
                o.user_id,
                o.order_number,
                o.order_dow,
                o.order_hour_of_day,
                o.days_since_prior_order,
                l.item_count,
                l.reordered_items,
                l.avg_cart_position,
                l.reordered_items::DOUBLE / l.item_count AS reorder_rate
            FROM {orders} o
            JOIN line_totals l USING (order_id)
        """
        copy_parquet(connection, order_mart_query, output_paths["order_mart"])

        customer_mart_query = f"""
            SELECT
                user_id,
                COUNT(*) AS completed_orders,
                SUM(item_count) AS purchased_units,
                AVG(item_count) AS avg_basket_size,
                AVG(days_since_prior_order) AS avg_days_between_orders,
                MAX(order_number) AS latest_order_number
            FROM {parquet_relation(output_paths["order_mart"])}
            GROUP BY user_id
        """
        copy_parquet(connection, customer_mart_query, output_paths["customer_mart"])

        product_mart_query = f"""
            SELECT
                c.product_id,
                c.product_name,
                c.aisle_id,
                c.aisle_name,
                c.department_id,
                c.department_name,
                COUNT(*) AS purchased_units,
                COUNT(DISTINCT o.user_id) AS customer_count,
                SUM(op.reordered) AS reordered_units,
                SUM(op.reordered)::DOUBLE / COUNT(*) AS reorder_rate,
                AVG(op.add_to_cart_order) AS avg_cart_position
            FROM {order_products} op
            JOIN {orders} o USING (order_id)
            JOIN {catalog} c USING (product_id)
            GROUP BY
                c.product_id, c.product_name, c.aisle_id, c.aisle_name,
                c.department_id, c.department_name
        """
        copy_parquet(connection, product_mart_query, output_paths["product_mart"])

        for name, group_column in (
            ("department_mart", "department_name"),
            ("aisle_mart", "aisle_name"),
        ):
            category_query = f"""
                SELECT
                    c.{group_column},
                    COUNT(*) AS purchased_units,
                    COUNT(DISTINCT op.order_id) AS order_count,
                    COUNT(DISTINCT o.user_id) AS customer_count,
                    COUNT(DISTINCT c.product_id) AS product_count,
                    SUM(op.reordered) AS reordered_units,
                    SUM(op.reordered)::DOUBLE / COUNT(*) AS reorder_rate
                FROM {order_products} op
                JOIN {orders} o USING (order_id)
                JOIN {catalog} c USING (product_id)
                GROUP BY c.{group_column}
            """
            copy_parquet(connection, category_query, output_paths[name])

        hourly_query = f"""
            SELECT
                order_dow,
                order_hour_of_day,
                COUNT(*) AS order_count,
                AVG(item_count) AS avg_basket_size
            FROM {parquet_relation(output_paths["order_mart"])}
            GROUP BY order_dow, order_hour_of_day
        """
        copy_parquet(connection, hourly_query, output_paths["hourly_mart"])

        source_counts = {
            "orders": connection.execute(
                f"SELECT COUNT(*) FROM {orders}"
            ).fetchone()[0],
            "prior_line_items": connection.execute(
                f"SELECT COUNT(*) FROM {order_products} WHERE source_set = 'prior'"
            ).fetchone()[0],
            "train_line_items": connection.execute(
                f"SELECT COUNT(*) FROM {order_products} WHERE source_set = 'train'"
            ).fetchone()[0],
            "completed_orders": connection.execute(
                f"SELECT COUNT(*) FROM {parquet_relation(output_paths['order_mart'])}"
            ).fetchone()[0],
        }
    finally:
        connection.close()

    metadata_path.write_text(
        json.dumps(
            {
                "source_files": {key: path.name for key, path in sources.items()},
                "counts": source_counts,
                "notes": [
                    "Completed orders are orders with labeled line items from the prior and train files.",
                    "The test partition has no labeled line items and is excluded from purchase metrics.",
                    "The source data contains no price, discount, or cost fields.",
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return source_counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="Folder containing the six source CSV files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/processed"),
        help="Folder for generated Parquet marts (default: data/processed).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing generated files in the output folder.",
    )
    args = parser.parse_args()
    counts = prepare_data(args.data_dir, args.output_dir, force=args.force)
    print(f"Prepared analytics marts in {args.output_dir.resolve()}")
    for name, count in counts.items():
        print(f"{name.replace('_', ' ').title()}: {count:,}")


if __name__ == "__main__":
    main()
