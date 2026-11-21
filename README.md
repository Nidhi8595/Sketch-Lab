# SketchLab - Interactive Approximate Analytics with Databricks

## 1. Project Overview

SketchLab is an interactive analytics application built using Databricks SQL, Delta tables, Python and Streamlit.

The project explores how mergeable probabilistic sketches can help answer analytical questions without retaining every original value in the summary.

It demonstrates two sketch techniques:

- **Theta sketches:** Estimate the number of distinct visitors and support set operations such as union.
- **KLL sketches:** Estimate percentiles such as p50, p90 and p99.

The application compares these approximate results against exact calculations performed on the original event data.

Users can select a date range and one or more campaigns, explore the results, understand the observed errors, and examine the serialized sketch sizes.

## 2. Problem Statement

Website analytics frequently involve large volumes of events.

Two common analytical questions are:

1. How many unique visitors accessed the website?
2. What were the typical and slowest response times?

Simply adding daily distinct visitor counts produces incorrect results because the same visitor may return on multiple days.

Similarly, averaging daily percentiles does not generally produce the percentile of the combined dataset.

SketchLab demonstrates how mergeable summaries can address these problems while retaining an approximate rather than exact result.

## 3. Architecture

The project has two main data tables:

| Table | Purpose |
|---|---|
| `workspace.sketchlab.raw_events` | Stores the original synthetic website events |
| `workspace.sketchlab.daily_sketches` | Stores daily Theta and KLL binary summaries |

The application uses the following architecture:

1. A user interacts with the Streamlit interface.
2. The app collects the date range and campaign selection.
3. Python constructs the required SQL queries.
4. The Databricks SQL Connector sends the queries to the configured SQL Warehouse.
5. The approximate query reads and merges the stored sketches.
6. The exact query calculates results from the raw event table.
7. Python compares the results and calculates percentage errors.
8. Streamlit displays the metrics, charts, tables and downloadable results.

The application runs under its Databricks App service principal identity. The identity receives the necessary SQL Warehouse resource access and Unity Catalog permissions.

## 4. Technology Stack

- Databricks Free Edition
- Databricks SQL Warehouse
- Unity Catalog
- Delta Lake
- Apache Spark SQL
- Theta sketches
- KLL sketches
- Python
- Streamlit
- Pandas
- Databricks SQL Connector
- Databricks SDK

## 5. Application Structure

```text
sketchlab_app/
│
├── app.py
│   └── Streamlit interface, SQL execution,
│       comparison logic and visualizations
│
├── app.yaml
│   └── Application startup command and
│       environment/resource configuration
│
└── requirements.txt
    └── Required Python dependencies
```

### requirements.txt

```text
databricks-sdk
databricks-sql-connector
streamlit
pandas
```

### app.yaml

```yaml
command: ['streamlit', 'run', 'app.py']

env:
  - name: DATABRICKS_WAREHOUSE_ID
    valueFrom: sql-warehouse

  - name: SKETCHLAB_CATALOG
    value: workspace
```

The `sql-warehouse` resource key connects the environment variable to the SQL Warehouse resource configured in Databricks Apps. This avoids hardcoding the Warehouse ID in the application.

## 6. Application Features

### Overview

Displays exact versus approximate distinct visitor counts and response-time percentiles, including the observed percentage error.

### Why Merging Works

Demonstrates why daily distinct estimates cannot simply be added and why daily percentiles cannot generally be averaged to obtain the combined percentile.

### Sketch Size

Displays the serialized sizes of the selected daily sketches and the merged Theta and KLL sketches.

### How It Works

Explains the purpose of sketches, Theta estimation, KLL percentiles, merging, and situations where exact calculations should be preferred.

### Additional interactive features

- Date-range selection
- Multiple campaign selection
- Exact-versus-approximate comparison
- Rank-check explanation
- CSV result export

## 7. Authentication and Permissions

Databricks creates an associated service principal for the application.

The application uses the Databricks SDK configuration and SQL Connector to authenticate and connect to the configured SQL Warehouse.

The app identity requires the appropriate permissions to:

- Use the SQL Warehouse.
- Use the `workspace` catalog.
- Use the `sketchlab` schema.
- Read the `raw_events` table.
- Read the `daily_sketches` table.

Application access and data access are separate permission concerns.

## 8. Key Implementation Decisions

- I kept the original event data separate from the compressed sketch summaries.
- I used the same date and campaign filters for exact and approximate calculations.
- I merged sketches rather than adding daily distinct counts or averaging daily percentiles.
- I used the exact calculations as the validation baseline.
- I measured serialized sketch sizes using `length()` on the binary sketch columns.
- I avoided making performance claims because this experiment did not establish a reliable benchmark.
- I used a Databricks App service principal instead of hardcoding personal credentials.
- I used an app resource reference for the SQL Warehouse instead of hardcoding its ID.

## 9. Important Experimental Findings

The results depend on the selected data and filters. In one demonstrated selection:

- Exact distinct visitors: 86,858
- Merged Theta estimate: 85,225
- Sum of daily distinct estimates: 184,667
- Exact p99: 2,192 ms
- Approximate p99: 2,545 ms
- Stored daily sketch bytes: 819,593
- Merged Theta sketch: 25,618 bytes
- Merged KLL sketch: 4,676 bytes

These results demonstrate why sketches must be merged using their supported operations and why approximate results must be validated against the relevant accuracy requirements.

## 10. Limitations

- The project uses synthetic website event data.
- Sketch results are approximate and may vary slightly.
- KLL percentile accuracy in rank does not guarantee the same error in milliseconds across different data distributions.
- The application does not claim a measured query-speed improvement.
- Serialized sketch size is not the same as the complete physical Delta table storage size.
- Exact calculations should be preferred when a value must be fully accurate, such as financial or compliance evidence.

## 11. Learning Outcomes

Through SketchLab, I explored:

- Mergeable probabilistic data structures.
- Approximate distinct counting and percentile estimation.
- Theta union and KLL merge operations.
- Exact-versus-approximate validation.
- Rank error versus value error.
- Binary sketch serialization and storage measurement.
- Databricks Apps architecture.
- Streamlit application development.
- SQL Warehouse connectivity using the Databricks SQL Connector.
- Service principal authentication and Unity Catalog permissions.

## 12. Conclusion

SketchLab turns an experiment with approximate analytics into an interactive Databricks application. It demonstrates how stored summaries can be merged to answer questions across selected periods, why naive aggregation can produce incorrect results, and how approximate outputs can be validated against exact calculations.

The project connects advanced analytical concepts with practical Databricks application development and security.
