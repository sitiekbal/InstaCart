import json
import tempfile
import unittest
from pathlib import Path

import duckdb

from src.prepare_data import find_sources, normalize_filename, prepare_data


class FileDiscoveryTests(unittest.TestCase):
    def test_normalizes_attached_filename(self):
        self.assertEqual(normalize_filename("Order Products Prior.csv"), "orderproductsprior")

    def test_finds_attached_file_names(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            for filename in (
                "Orders.csv",
                "Order Products Prior.csv",
                "Order Products Train.csv",
                "Products.csv",
                "Aisle.csv",
                "Departments.csv",
            ):
                (data_dir / filename).touch()

            sources = find_sources(data_dir)

            self.assertEqual(set(sources), {"orders", "prior", "train", "products", "aisles", "departments"})


class DataPreparationTests(unittest.TestCase):
    def write_fixture(self, data_dir: Path):
        files = {
            "orders.csv": (
                "order_id,user_id,eval_set,order_number,order_dow,order_hour_of_day,days_since_prior_order\n"
                "1,1,prior,1,2,8,\n"
                "2,1,prior,2,3,9,7\n"
                "3,1,train,3,4,10,14\n"
                "4,2,prior,1,0,11,\n"
                "5,2,test,2,1,12,30\n"
            ),
            "order_products__prior.csv": (
                "order_id,product_id,add_to_cart_order,reordered\n"
                "1,10,1,0\n"
                "1,11,2,0\n"
                "2,10,1,1\n"
                "4,12,1,0\n"
            ),
            "order_products__train.csv": (
                "order_id,product_id,add_to_cart_order,reordered\n"
                "3,10,1,1\n"
                "3,12,2,0\n"
            ),
            "products.csv": (
                "product_id,product_name,aisle_id,department_id\n"
                "10,Apples,1,1\n"
                "11,Bread,2,2\n"
                "12,Milk,1,1\n"
            ),
            "aisles.csv": "aisle_id,aisle\n1,produce\n2,bakery\n",
            "departments.csv": "department_id,department\n1,fresh\n2,bakery\n",
        }
        for filename, contents in files.items():
            (data_dir / filename).write_text(contents, encoding="utf-8")

    def test_builds_marts_and_excludes_unlabeled_test_order(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            data_dir = root / "source"
            output_dir = root / "processed"
            data_dir.mkdir()
            self.write_fixture(data_dir)

            counts = prepare_data(data_dir, output_dir)

            self.assertEqual(
                counts,
                {
                    "orders": 5,
                    "prior_line_items": 4,
                    "train_line_items": 2,
                    "completed_orders": 4,
                },
            )
            connection = duckdb.connect()
            try:
                order_count = connection.execute(
                    f"SELECT COUNT(*) FROM read_parquet('{output_dir / 'order_mart.parquet'}')"
                ).fetchone()[0]
                item_count = connection.execute(
                    f"SELECT SUM(item_count) FROM read_parquet('{output_dir / 'order_mart.parquet'}')"
                ).fetchone()[0]
                product_count = connection.execute(
                    f"""
                    SELECT purchased_units
                    FROM read_parquet('{output_dir / 'product_mart.parquet'}')
                    WHERE product_id = 10
                    """
                ).fetchone()[0]
            finally:
                connection.close()

            self.assertEqual(order_count, 4)
            self.assertEqual(item_count, 6)
            self.assertEqual(product_count, 3)
            metadata = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["counts"]["completed_orders"], 4)

    def test_refuses_to_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            data_dir = root / "source"
            output_dir = root / "processed"
            data_dir.mkdir()
            self.write_fixture(data_dir)
            output_dir.mkdir()
            (output_dir / "orders.parquet").touch()

            with self.assertRaises(FileExistsError):
                prepare_data(data_dir, output_dir)


if __name__ == "__main__":
    unittest.main()
