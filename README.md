<p align="center">
  <img src="assets/market-analytics-logo.svg" alt="Market Analytics — E-commerce Analytics Capstone" width="420">
</p>

<h1 align="center">E-commerce Analytics Capstone</h1>

An end-to-end analytics project built around the Instacart Market Basket Analysis data. It turns the source CSVs into compact analytics marts, then uses an interactive dashboard to explore shopping cadence, basket size, product popularity, and repeat purchasing.

## Business question

How can an online grocery retailer use order timing, cart behavior, and catalog data to understand demand and encourage repeat purchases?

The project answers this with four investigation areas:

- When do customers place orders, and how long do they wait between orders?
- Which departments, aisles, and products appear most often in baskets?
- How does reorder behavior vary across products and departments?
- What are typical basket sizes, and how does basket composition change with cart position?

## Data

The source data contains orders, order line items, products, aisles, and departments. The data dictionary describes the fields and their relationships.

Keep the original CSVs outside version control. Put the six files in a local folder using either the attached names or the standard Kaggle names:

| Attached file | Standard Kaggle filename |
| --- | --- |
| `Orders.csv` | `orders.csv` |
| `Order Products Prior.csv` | `order_products__prior.csv` |
| `Order Products Train.csv` | `order_products__train.csv` |
| `Products.csv` | `products.csv` |
| `Aisle.csv` | `aisles.csv` |
| `Departments.csv` | `departments.csv` |

The preparation script recognizes either naming convention. The `test` partition contains orders without labeled products, so it is excluded from purchase-level metrics. The prior and train line-item files are combined for completed-order analysis.

**Important limitation:** the supplied data has no prices, discounts, or costs. This project reports units, orders, basket sizes, and reorder rates—not revenue, basket value, or profit. Reorder is a line-item flag, not a causal measure of loyalty.

## Run locally

Python 3.10+ is recommended.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Use a folder containing the six source CSVs
python src/prepare_data.py --data-dir /path/to/instacart-csvs
streamlit run app.py
```

Processed Parquet marts are written to `data/processed/` by default and are excluded from Git. To use a different output folder, pass `--output-dir` during preparation and set `INSTACART_PROCESSED_DIR` before starting the dashboard:

```bash
python src/prepare_data.py --data-dir /path/to/instacart-csvs --output-dir /path/to/processed
export INSTACART_PROCESSED_DIR=/path/to/processed
streamlit run app.py
```

The first preparation run reads the large CSVs and can take a few minutes. Subsequent dashboard queries use the compact Parquet marts.

Run the data-preparation tests with:

```bash
python -m unittest discover -s tests
```

## Project structure

```text
app.py                  Streamlit dashboard
src/prepare_data.py     CSV validation, transformations, and analytics marts
tests/                  Unit tests for file discovery and data preparation
data/processed/         Generated Parquet marts (not committed)
```

## Metrics and definitions

- **Completed order:** an order with labeled line items in either prior or train data.
- **Basket size:** number of line items in a completed order.
- **Reorder rate:** reordered line items divided by all line items.
- **Customer order count:** number of completed orders with labeled products.
- **Cart position:** the source `add_to_cart_order`, treated as a sequence rather than a timestamp.

Days of week follow the source encoding: `0` is Sunday and `6` is Saturday. The data does not include actual calendar dates, so the dashboard shows relative order sequence and timing rather than seasonality over calendar time.

## Suggested capstone deliverables

1. Use the dashboard to identify high-volume categories, peak ordering windows, and products with strong repeat-purchase signals.
2. Validate findings across product and department levels; note sample-size limitations for low-volume products.
3. Recommend a demand-planning or merchandising action and define a measurable follow-up experiment.
4. Present the data limitations alongside conclusions, especially the absence of price and calendar-date fields.
