import os
from datetime import date
import pandas as pd
import streamlit as st
from databricks import sql
from databricks.sdk.core import Config

CATALOG = os.getenv("SKETCHLAB_CATALOG", "workspace")
RAW = f"{CATALOG}.sketchlab.raw_events"
DAILY = f"{CATALOG}.sketchlab.daily_sketches"
CAMPAIGNS = ["Campaign_A", "Campaign_B", "Campaign_C", "Campaign_D"]

cfg = Config()

@st.cache_resource
def get_connection():
    host = cfg.host.replace("https://", "").replace("http://", "")
    return sql.connect(
        server_hostname=host,
        http_path=f"/sql/1.0/warehouses/{os.getenv('DATABRICKS_WAREHOUSE_ID')}",
        credentials_provider=lambda: cfg.authenticate,
    )
def run(query):
    with get_connection().cursor() as cur:
        cur.execute(query)
        return cur.fetchall_arrow().to_pylist()[0]
def pct(approx, exact):
    return (approx - exact) / exact * 100 if exact else 0.0

st.set_page_config(page_title="SketchLab", layout="wide")
st.title("SketchLab")
st.caption("Approximate answers from stored sketches vs exact answers from raw events (synthetic data).")

with st.form("controls"):
    left, right = st.columns(2)
    dates = left.date_input(
        "Date range",
        value=(date(2026, 10, 3), date(2026, 10, 9)),
        min_value=date(2026, 10, 1),
        max_value=date(2026, 10, 14),
    )
    campaigns = right.multiselect("Campaigns", CAMPAIGNS, default=CAMPAIGNS)
    go = st.form_submit_button("Compare")

if not go:
    st.info("Choose a date range and campaigns, then press Compare.")
    st.stop()
if len(dates) != 2 or not campaigns:
    st.warning("Pick a start date, an end date and at least one campaign.")
    st.stop()

start, end = dates
# Dates and campaigns come from fixed pickers, so building this filter text is safe here.
camps = ", ".join(f"'{c}'" for c in campaigns)
where = f"event_date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}' AND campaign IN ({camps})"

with st.spinner("Running queries on the SQL warehouse..."):
    approx = run(f"""
        WITH m AS (
          SELECT theta_union_agg(theta_sketch) AS t,
                 kll_merge_agg_bigint(kll_sketch) AS k,
                 COUNT(*) AS sketch_rows,
                 SUM(event_count) AS events,
                 SUM(length(theta_sketch) + length(kll_sketch)) AS daily_bytes
          FROM {DAILY} WHERE {where}
        )
        SELECT theta_sketch_estimate(t) AS distinct_visitors,
               kll_sketch_get_quantile_bigint(k, 0.50) AS p50,
               kll_sketch_get_quantile_bigint(k, 0.90) AS p90,
               kll_sketch_get_quantile_bigint(k, 0.99) AS p99,
               length(t) AS merged_theta_bytes,
               length(k) AS merged_kll_bytes,
               sketch_rows, events, daily_bytes
        FROM m
    """)
    exact = run(f"""
        SELECT COUNT(*) AS events,
               COUNT(DISTINCT visitor_id) AS distinct_visitors,
               percentile_disc(0.50) WITHIN GROUP (ORDER BY response_time_ms) AS p50,
               percentile_disc(0.90) WITHIN GROUP (ORDER BY response_time_ms) AS p90,
               percentile_disc(0.99) WITHIN GROUP (ORDER BY response_time_ms) AS p99
        FROM {RAW} WHERE {where}
    """)

if not approx["events"]:
    st.warning("No data for this selection.")
    st.stop()
if int(approx["events"]) != int(exact["events"]):
    st.error("The sketches and the raw events do not cover the same number of events.")

st.subheader("Unique visitors")
c1, c2, c3 = st.columns(3)
c1.metric("Exact", f"{int(exact['distinct_visitors']):,}")
c2.metric(
    "Approx (Theta)",
    f"{float(approx['distinct_visitors']):,.0f}",
    f"{pct(float(approx['distinct_visitors']), float(exact['distinct_visitors'])):+.2f}% vs exact",
    delta_color="off",
)
c3.metric("Events in selection", f"{int(exact['events']):,}")

st.subheader("Latency percentiles (ms)")
rows = []
for name in ["p50", "p90", "p99"]:
    e, a = float(exact[name]), float(approx[name])
    rows.append({"metric": name, "exact_ms": e, "approx_ms": a,
                 "difference_ms": a - e, "error_pct": round(pct(a, e), 3)})
lat = pd.DataFrame(rows).set_index("metric")
st.dataframe(lat, use_container_width=True)

st.subheader("Observed error (%)")
err = pd.DataFrame(
    {"error_pct": [pct(float(approx["distinct_visitors"]), float(exact["distinct_visitors"]))] + list(lat["error_pct"])},
    index=["distinct_visitors", "p50", "p90", "p99"],
)
st.bar_chart(err)

st.subheader("Sketch size (serialized bytes)")
s1, s2, s3 = st.columns(3)
s1.metric("Merged Theta sketch", f"{int(approx['merged_theta_bytes']):,}")
s2.metric("Merged KLL sketch", f"{int(approx['merged_kll_bytes']):,}")
s3.metric(f"{int(approx['sketch_rows'])} daily sketches stored", f"{int(approx['daily_bytes']):,}")

st.subheader("What this means")
st.markdown(
    """
- **Exact** values are calculated from the raw events. **Approx** values come from stored sketches that were merged for your selection.
- **Error %** = (approx − exact) / exact. A negative value means the sketch was below the exact value.
- **Distinct visitors** are not added day by day. The Theta sketches are merged, so a repeat visitor counts once.
- **Percentiles** come from merged KLL sketches. They are accurate in rank, and in the slow tail a small rank difference can be a large number of milliseconds.
- In my experiments the KLL values changed slightly between runs of the same query, while the Theta estimate did not.
- This demo does not measure speed, and these results are not a guarantee for other data.
"""
)