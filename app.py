from __future__ import annotations

import os
from pathlib import Path

import duckdb
import plotly.express as px
import streamlit as st


DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
MARTS = (
    "order_mart",
    "customer_mart",
    "product_mart",
    "department_mart",
    "aisle_mart",
    "hourly_mart",
)

st.set_page_config(
    page_title="Instacart Market Analytics",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(
    """
    <style>
      .block-container { padding-top: 2rem; padding-bottom: 3rem; }
      [data-testid="stMetric"] {
        background: #f5f8f4;
        border: 1px solid #e2eae0;
        padding: 1rem;
        border-radius: 0.75rem;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=3600)
def query_frame(processed_dir: str, query: str, parameters: tuple = ()):
    connection = duckdb.connect(database=":memory:")
    root = Path(processed_dir).resolve()
    try:
        for mart in MARTS:
            path = str(root / f"{mart}.parquet").replace("'", "''")
            connection.execute(
                f"CREATE VIEW {mart} AS SELECT * FROM read_parquet('{path}')"
            )
        return connection.execute(query, list(parameters)).fetchdf()
    finally:
        connection.close()


def format_count(value: int | float) -> str:
    return f"{value:,.0f}"


def format_percent(value: float) -> str:
    return f"{value:.1%}"


st.title("Instacart Market Analytics")
st.caption(
    "An e-commerce analytics capstone exploring grocery demand, basket behavior, "
    "and repeat purchasing."
)

processed_dir = str(
    Path(os.environ.get("INSTACART_PROCESSED_DIR", "data/processed")).resolve()
)
required_files = [Path(processed_dir) / f"{mart}.parquet" for mart in MARTS]
missing_files = [path.name for path in required_files if not path.is_file()]
if missing_files:
    st.error(
        "Analytics marts are missing: "
        + ", ".join(missing_files)
        + ". Prepare the source data before opening the dashboard."
    )
    st.code(
        "python src/prepare_data.py --data-dir /path/to/instacart-csvs",
        language="bash",
    )
    st.stop()

summary = query_frame(
    processed_dir,
    """
    SELECT
        COUNT(*) AS completed_orders,
        SUM(item_count) AS purchased_units,
        AVG(item_count) AS avg_basket_size,
        SUM(reordered_items)::DOUBLE / SUM(item_count) AS reorder_rate
    FROM order_mart
    """,
).iloc[0]
customer_summary = query_frame(
    processed_dir,
    """
    SELECT
        COUNT(*) AS customers,
        SUM(CASE WHEN completed_orders > 1 THEN 1 ELSE 0 END)::DOUBLE / COUNT(*)
            AS repeat_customer_rate
    FROM customer_mart
    """,
).iloc[0]

with st.sidebar:
    st.header("Project guide")
    st.markdown(
        """
        **Business question**

        How can order timing, cart behavior, and catalog data help an online
        grocer understand demand and encourage repeat purchases?

        **Explore**
        - Overview: demand by day and hour
        - Products: top categories and reorder signals
        - Customer behavior: order frequency and basket habits
        """
    )
    st.divider()
    st.caption(
        "Day-of-week values follow the source encoding (0 = Sunday). "
        "No price or calendar-date fields are available."
    )

metric_columns = st.columns(5)
metrics = [
    ("Completed orders", format_count(summary["completed_orders"])),
    ("Customers", format_count(customer_summary["customers"])),
    ("Items purchased", format_count(summary["purchased_units"])),
    ("Avg. basket size", f"{summary['avg_basket_size']:.1f} items"),
    ("Reorder rate", format_percent(summary["reorder_rate"])),
]
for column, (label, value) in zip(metric_columns, metrics):
    column.metric(label, value)

tabs = st.tabs(["Overview", "Products & categories", "Customer behavior"])

with tabs[0]:
    st.subheader("When do customers shop?")
    timing = query_frame(
        processed_dir,
        """
        SELECT order_dow, order_hour_of_day, order_count
        FROM hourly_mart
        ORDER BY order_dow, order_hour_of_day
        """,
    )
    timing["day"] = timing["order_dow"].map(
        lambda value: (
            DAY_NAMES[int(value)]
            if 0 <= int(value) < len(DAY_NAMES)
            else "Unknown"
        )
    )
    heatmap = timing.pivot(
        index="day", columns="order_hour_of_day", values="order_count"
    ).reindex(DAY_NAMES).fillna(0)
    heatmap.columns = [f"{int(hour):02d}:00" for hour in heatmap.columns]
    heatmap_chart = px.imshow(
        heatmap,
        aspect="auto",
        color_continuous_scale="YlGn",
        labels={"x": "Hour of day", "y": "Day of week", "color": "Orders"},
    )
    heatmap_chart.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=380)

    department_summary = query_frame(
        processed_dir,
        """
        SELECT department_name, purchased_units, reorder_rate
        FROM department_mart
        ORDER BY purchased_units DESC
        LIMIT 12
        """,
    )
    department_chart = px.bar(
        department_summary.sort_values("purchased_units"),
        x="purchased_units",
        y="department_name",
        orientation="h",
        color="reorder_rate",
        color_continuous_scale="Teal",
        labels={
            "purchased_units": "Items purchased",
            "department_name": "",
            "reorder_rate": "Reorder rate",
        },
    )
    department_chart.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=380)
    left, right = st.columns([1.4, 1])
    left.plotly_chart(heatmap_chart, use_container_width=True)
    right.plotly_chart(department_chart, use_container_width=True)

    st.info(
        f"{format_percent(customer_summary['repeat_customer_rate'])} of customers "
        "have more than one labeled completed order. This is a descriptive signal, "
        "not proof that any particular product or experience caused retention."
    )

with tabs[1]:
    st.subheader("What is selling, and what gets reordered?")
    departments = query_frame(
        processed_dir,
        "SELECT DISTINCT department_name FROM product_mart ORDER BY department_name",
    )["department_name"].dropna().tolist()
    selected_department = st.selectbox("Department", ["All departments", *departments])
    minimum_units = st.slider("Minimum product purchases", 1, 1000, 100, step=25)

    if selected_department == "All departments":
        products = query_frame(
            processed_dir,
            """
            SELECT product_name, department_name, purchased_units,
                   customer_count, reorder_rate, avg_cart_position
            FROM product_mart
            WHERE purchased_units >= ?
            ORDER BY purchased_units DESC
            LIMIT 20
            """,
            (minimum_units,),
        )
    else:
        products = query_frame(
            processed_dir,
            """
            SELECT product_name, department_name, purchased_units,
                   customer_count, reorder_rate, avg_cart_position
            FROM product_mart
            WHERE department_name = ? AND purchased_units >= ?
            ORDER BY purchased_units DESC
            LIMIT 20
            """,
            (selected_department, minimum_units),
        )

    if products.empty:
        st.warning("No products match these filters. Lower the purchase threshold.")
    else:
        product_chart = px.bar(
            products.sort_values("purchased_units"),
            x="purchased_units",
            y="product_name",
            orientation="h",
            color="reorder_rate",
            color_continuous_scale="Teal",
            hover_data=["customer_count", "avg_cart_position"],
            labels={
                "purchased_units": "Items purchased",
                "product_name": "",
                "reorder_rate": "Reorder rate",
                "customer_count": "Customers",
                "avg_cart_position": "Avg. cart position",
            },
        )
        product_chart.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=600)
        st.plotly_chart(product_chart, use_container_width=True)
        st.dataframe(
            products.rename(
                columns={
                    "product_name": "Product",
                    "department_name": "Department",
                    "purchased_units": "Items purchased",
                    "customer_count": "Customers",
                    "reorder_rate": "Reorder rate",
                    "avg_cart_position": "Avg. cart position",
                }
            ).style.format(
                {
                    "Items purchased": "{:,.0f}",
                    "Customers": "{:,.0f}",
                    "Reorder rate": "{:.1%}",
                    "Avg. cart position": "{:.1f}",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

    category_summary = query_frame(
        processed_dir,
        """
        SELECT aisle_name, purchased_units, reorder_rate
        FROM aisle_mart
        ORDER BY purchased_units DESC
        LIMIT 15
        """,
    )
    st.markdown("#### Most active aisles")
    aisle_chart = px.bar(
        category_summary.sort_values("purchased_units"),
        x="purchased_units",
        y="aisle_name",
        orientation="h",
        color="reorder_rate",
        color_continuous_scale="Teal",
        labels={
            "purchased_units": "Items purchased",
            "aisle_name": "",
            "reorder_rate": "Reorder rate",
        },
    )
    aisle_chart.update_layout(margin=dict(l=0, r=0, t=0, b=0), height=420)
    st.plotly_chart(aisle_chart, use_container_width=True)

with tabs[2]:
    st.subheader("How do shopping habits vary?")
    customer_behavior = query_frame(
        processed_dir,
        """
        SELECT completed_orders, COUNT(*) AS customer_count
        FROM customer_mart
        GROUP BY completed_orders
        ORDER BY completed_orders
        """,
    )
    gap_summary = query_frame(
        processed_dir,
        """
        SELECT
            AVG(days_since_prior_order) AS avg_days_between_orders,
            MEDIAN(days_since_prior_order) AS median_days_between_orders,
            MEDIAN(item_count) AS median_basket_size
        FROM order_mart
        WHERE days_since_prior_order IS NOT NULL
        """,
    ).iloc[0]

    behavior_metrics = st.columns(3)
    behavior_metrics[0].metric(
        "Repeat customers",
        format_percent(customer_summary["repeat_customer_rate"]),
    )
    behavior_metrics[1].metric(
        "Median reorder interval",
        f"{gap_summary['median_days_between_orders']:.0f} days",
    )
    behavior_metrics[2].metric(
        "Median basket size", f"{gap_summary['median_basket_size']:.0f} items"
    )

    customer_behavior["order_group"] = customer_behavior["completed_orders"].astype(str)
    customer_behavior.loc[
        customer_behavior["completed_orders"] >= 20, "order_group"
    ] = "20+"
    grouped_behavior = (
        customer_behavior.groupby("order_group", as_index=False)["customer_count"]
        .sum()
        .sort_values(
            "order_group",
            key=lambda values: values.map(
                lambda value: 20 if value == "20+" else int(value)
            ),
        )
    )
    behavior_chart = px.bar(
        grouped_behavior,
        x="order_group",
        y="customer_count",
        labels={
            "order_group": "Completed orders per customer",
            "customer_count": "Customers",
        },
        color_discrete_sequence=["#3c8c63"],
    )
    behavior_chart.update_layout(margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(behavior_chart, use_container_width=True)

    st.markdown("#### Basket and cart behavior")
    basket_summary = query_frame(
        processed_dir,
        """
        SELECT item_count, COUNT(*) AS order_count
        FROM order_mart
        GROUP BY item_count
        ORDER BY item_count
        """,
    )
    st.area_chart(
        basket_summary.set_index("item_count")["order_count"],
        height=280,
        color="#75a98b",
    )
    st.caption(
        f"The average gap is {gap_summary['avg_days_between_orders']:.1f} days "
        "among orders with a recorded prior-order interval. Cart position records "
        "sequence, not elapsed time."
    )

with st.expander("Interpretation and data limitations"):
    st.markdown(
        """
        - The dashboard describes observed orders; it does not estimate causal effects.
        - Products with few purchases can have unstable reorder rates. Use the
          purchase threshold before comparing products.
        - Test-set orders do not have labeled products and are not included in
          purchase metrics.
        - There are no price, promotion, margin, or calendar-date fields, so
          revenue, profitability, promotional lift, and calendar seasonality cannot
          be measured from these files.
        """
    )
