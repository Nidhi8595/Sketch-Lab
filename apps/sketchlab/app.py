import os
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st
from databricks import sql
from databricks.sdk.core import Config

CATALOG = os.getenv("SKETCHLAB_CATALOG", "workspace")
RAW = f"{CATALOG}.sketchlab.raw_events"
DAILY = f"{CATALOG}.sketchlab.daily_sketches"
CAMPAIGNS = ["Campaign_A", "Campaign_B", "Campaign_C", "Campaign_D"]
MIN_DAY, MAX_DAY = date(2026, 10, 1), date(2026, 10, 14)
PRESETS = {
    "Oct 3 - 9 (my test range)": (date(2026, 10, 3), date(2026, 10, 9)),
    "Week 1 (Oct 1 - 7)": (date(2026, 10, 1), date(2026, 10, 7)),
    "Week 2 (Oct 8 - 14)": (date(2026, 10, 8), date(2026, 10, 14)),
    "Full dataset (Oct 1 - 14)": (MIN_DAY, MAX_DAY),
    "Single day (Oct 5)": (date(2026, 10, 5), date(2026, 10, 5)),
    "Custom range": None,
}
INDIGO, CYAN, SLATE, RED = "#6366f1", "#06b6d4", "#94a3b8", "#ef4444"

cfg = Config()

st.set_page_config(page_title="SketchLab", page_icon="🧪", layout="wide")

st.markdown(
    """
<style>
.block-container {padding-top: 1.4rem; max-width: 1300px;}
.hero {background: linear-gradient(120deg,#4f46e5 0%,#7c3aed 50%,#06b6d4 100%);
       border-radius: 18px; padding: 26px 32px; margin-bottom: 14px; color: #ffffff;}
.hero h1 {margin: 0; font-size: 2.1rem; color: #ffffff; padding: 0;}
.hero p {margin: 6px 0 0 0; opacity: .93; font-size: 1.02rem; color: #ffffff;}
.pill {display: inline-block; padding: 3px 11px; border-radius: 999px; font-size: .78rem;
       background: rgba(255,255,255,.2); margin: 12px 6px 0 0; color: #ffffff;}
.kpi {border: 1px solid rgba(128,128,128,.28); background: rgba(128,128,128,.07);
      border-radius: 16px; padding: 16px 18px; height: 100%;}
.kpi .lbl {font-size: .76rem; opacity: .7; text-transform: uppercase; letter-spacing: .05em;}
.kpi .val {font-size: 1.85rem; font-weight: 650; line-height: 1.25; margin-top: 4px;}
.kpi .sub {font-size: .84rem; opacity: .8; margin-top: 4px;}
.badge {display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: .78rem; font-weight: 600;}
.good {background: rgba(16,185,129,.18); color: #10b981;}
.warn {background: rgba(245,158,11,.18); color: #f59e0b;}
.bad {background: rgba(239,68,68,.18); color: #ef4444;}
.note {border-left: 4px solid #6366f1; padding: 8px 14px; background: rgba(99,102,241,.08);
       border-radius: 0 10px 10px 0; font-size: .92rem; margin: 6px 0 10px 0;}
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_resource
def get_connection():
    host = cfg.host.replace("https://", "").replace("http://", "")
    return sql.connect(
        server_hostname=host,
        http_path=f"/sql/1.0/warehouses/{os.getenv('DATABRICKS_WAREHOUSE_ID')}",
        credentials_provider=lambda: cfg.authenticate,
    )


def query(text):
    with get_connection().cursor() as cur:
        cur.execute(text)
        return cur.fetchall_arrow().to_pylist()


@st.cache_data(ttl=900, show_spinner=False)
def fetch(start_iso, end_iso, campaigns):
    # Dates and campaigns come from fixed pickers, so building this filter text is safe here.
    camps = ", ".join(f"'{c}'" for c in campaigns)
    where = f"event_date BETWEEN '{start_iso}' AND '{end_iso}' AND campaign IN ({camps})"

    approx = query(f"""
        WITH m AS (
          SELECT theta_union_agg(theta_sketch)    AS t,
                 kll_merge_agg_bigint(kll_sketch) AS k,
                 COUNT(*)                         AS sketch_rows,
                 SUM(event_count)                 AS events,
                 SUM(length(theta_sketch) + length(kll_sketch)) AS daily_bytes,
                 SUM(theta_sketch_estimate(theta_sketch))       AS naive_sum_distinct,
                 AVG(kll_sketch_get_quantile_bigint(kll_sketch, 0.50)) AS naive_p50,
                 AVG(kll_sketch_get_quantile_bigint(kll_sketch, 0.90)) AS naive_p90,
                 AVG(kll_sketch_get_quantile_bigint(kll_sketch, 0.99)) AS naive_p99
          FROM {DAILY} WHERE {where}
        )
        SELECT theta_sketch_estimate(t)                AS distinct_visitors,
               kll_sketch_get_quantile_bigint(k, 0.50) AS p50,
               kll_sketch_get_quantile_bigint(k, 0.90) AS p90,
               kll_sketch_get_quantile_bigint(k, 0.99) AS p99,
               length(t) AS merged_theta_bytes,
               length(k) AS merged_kll_bytes,
               sketch_rows, events, daily_bytes,
               naive_sum_distinct, naive_p50, naive_p90, naive_p99
        FROM m
    """)[0]
    if not approx["events"]:
        return None

    exact = query(f"""
        SELECT COUNT(*)                                                       AS events,
               COUNT(DISTINCT visitor_id)                                     AS distinct_visitors,
               percentile_disc(0.50) WITHIN GROUP (ORDER BY response_time_ms) AS p50,
               percentile_disc(0.90) WITHIN GROUP (ORDER BY response_time_ms) AS p90,
               percentile_disc(0.99) WITHIN GROUP (ORDER BY response_time_ms) AS p99
        FROM {RAW} WHERE {where}
    """)[0]

    ranks = query(f"""
        SELECT AVG(CASE WHEN response_time_ms <= {int(approx['p50'])} THEN 1.0 ELSE 0.0 END) AS r50,
               AVG(CASE WHEN response_time_ms <= {int(approx['p90'])} THEN 1.0 ELSE 0.0 END) AS r90,
               AVG(CASE WHEN response_time_ms <= {int(approx['p99'])} THEN 1.0 ELSE 0.0 END) AS r99
        FROM {RAW} WHERE {where}
    """)[0]

    daily_approx = query(f"""
        SELECT event_date, theta_sketch_estimate(theta_union_agg(theta_sketch)) AS approx
        FROM {DAILY} WHERE {where} GROUP BY event_date ORDER BY event_date
    """)
    daily_exact = query(f"""
        SELECT event_date, COUNT(DISTINCT visitor_id) AS exact
        FROM {RAW} WHERE {where} GROUP BY event_date ORDER BY event_date
    """)
    return approx, exact, ranks, daily_approx, daily_exact


def pct(approx_value, exact_value):
    return (approx_value - exact_value) / exact_value * 100 if exact_value else 0.0


def tone(error_pct):
    size = abs(error_pct)
    return "good" if size < 2 else "warn" if size < 5 else "bad"


def kpi(label, value, sub="", badge=None, badge_tone="good"):
    tag = f'<span class="badge {badge_tone}">{badge}</span> ' if badge else ""
    return (f'<div class="kpi"><div class="lbl">{label}</div><div class="val">{value}</div>'
            f'<div class="sub">{tag}{sub}</div></div>')


# ---------------- sidebar ----------------
st.sidebar.header("Controls")
preset = st.sidebar.selectbox("Date range", list(PRESETS.keys()))
if PRESETS[preset] is None:
    picked = st.sidebar.date_input("Pick start and end", value=(date(2026, 10, 3), date(2026, 10, 9)),
                                   min_value=MIN_DAY, max_value=MAX_DAY)
    if len(picked) != 2:
        st.sidebar.info("Pick both a start date and an end date.")
        st.stop()
    start, end = picked
else:
    start, end = PRESETS[preset]
campaigns = st.sidebar.multiselect("Campaigns", CAMPAIGNS, default=CAMPAIGNS)
if not campaigns:
    st.sidebar.warning("Select at least one campaign.")
    st.stop()

st.sidebar.divider()
if st.sidebar.button("Re-run queries"):
    st.cache_data.clear()
    st.rerun()
st.sidebar.caption("Results are cached for 15 minutes. Re-run to rebuild the merged sketches. "
                   "KLL percentiles can change slightly between runs.")
st.sidebar.divider()
st.sidebar.caption("Data: 400,000 synthetic website events, 14 days, 4 campaigns, 6 pages.")

# ---------------- hero ----------------
st.markdown(
    '<div class="hero"><h1>SketchLab</h1>'
    "<p>Approximate answers from tiny stored sketches, checked against exact answers from the raw events.</p>"
    '<span class="pill">Theta sketch</span><span class="pill">KLL sketch</span>'
    '<span class="pill">Delta tables</span><span class="pill">Synthetic data</span></div>',
    unsafe_allow_html=True,
)
st.caption(f"Selection: {start:%b %d} to {end:%b %d}  |  {', '.join(campaigns)}")

with st.spinner("Querying the SQL warehouse..."):
    result = fetch(start.isoformat(), end.isoformat(), tuple(campaigns))
if result is None:
    st.warning("No data for this selection.")
    st.stop()
approx, exact, ranks, daily_approx, daily_exact = result

d_exact = float(exact["distinct_visitors"])
d_approx = float(approx["distinct_visitors"])
d_err = pct(d_approx, d_exact)
same_records = int(approx["events"]) == int(exact["events"])
merged_bytes = int(approx["merged_theta_bytes"]) + int(approx["merged_kll_bytes"])
daily_bytes = int(approx["daily_bytes"])
sketch_rows = int(approx["sketch_rows"])

k1, k2, k3, k4 = st.columns(4)
k1.markdown(kpi("Exact distinct visitors", f"{int(d_exact):,}", "counted from raw events"), unsafe_allow_html=True)
k2.markdown(kpi("Sketch estimate (Theta)", f"{int(round(d_approx)):,}", "vs exact",
                f"{d_err:+.2f}%", tone(d_err)), unsafe_allow_html=True)
k3.markdown(kpi("Events in selection", f"{int(exact['events']):,}",
                "same records in both" if same_records else "records do NOT match",
                "match" if same_records else "check", "good" if same_records else "bad"),
            unsafe_allow_html=True)
k4.markdown(kpi("Merged sketch size", f"{merged_bytes:,} B",
                f"from {sketch_rows} daily sketches ({daily_bytes:,} B)"), unsafe_allow_html=True)
st.write("")

latency = []
for name in ["p50", "p90", "p99"]:
    e, a = float(exact[name]), float(approx[name])
    latency.append({"metric": name, "exact_ms": e, "sketch_ms": a, "difference_ms": a - e,
                    "error_pct": round(pct(a, e), 3)})
lat = pd.DataFrame(latency)

tab1, tab2, tab3, tab4 = st.tabs(["Overview", "Why merging works", "Sketch size", "How it works"])

# ---------------- tab 1: overview ----------------
with tab1:
    left, right = st.columns(2)
    with left:
        st.subheader("Latency percentiles")
        long = lat.melt(id_vars="metric", value_vars=["exact_ms", "sketch_ms"], var_name="kind", value_name="ms")
        long["kind"] = long["kind"].map({"exact_ms": "Exact", "sketch_ms": "Sketch"})
        chart = (
            alt.Chart(long)
            .mark_bar(cornerRadiusEnd=5)
            .encode(
                x=alt.X("kind:N", title=None, axis=None),
                y=alt.Y("ms:Q", scale=alt.Scale(type="log"), title="milliseconds (log scale)"),
                color=alt.Color("kind:N", scale=alt.Scale(domain=["Exact", "Sketch"], range=[SLATE, INDIGO]),
                                title=None),
                column=alt.Column("metric:N", title=None, header=alt.Header(labelOrient="bottom")),
                tooltip=["metric", "kind", alt.Tooltip("ms:Q", format=",.0f")],
            )
            .properties(width=110, height=260)
        )
        st.altair_chart(chart)
    with right:
        st.subheader("Observed error")
        err = pd.DataFrame({
            "metric": ["distinct visitors", "p50", "p90", "p99"],
            "error_pct": [d_err] + list(lat["error_pct"]),
        })
        err["direction"] = err["error_pct"].apply(lambda v: "below exact" if v < 0 else "above exact")
        bars = (
            alt.Chart(err)
            .mark_bar(cornerRadiusEnd=6)
            .encode(
                x=alt.X("metric:N", sort=None, title=None, axis=alt.Axis(labelAngle=0)),
                y=alt.Y("error_pct:Q", title="error vs exact (%)"),
                color=alt.Color("direction:N",
                                scale=alt.Scale(domain=["below exact", "above exact"], range=[INDIGO, CYAN]),
                                legend=None),
                tooltip=["metric", alt.Tooltip("error_pct:Q", format="+.3f")],
            )
            .properties(height=260)
        )
        zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="gray").encode(y="y:Q")
        st.altair_chart(bars + zero, use_container_width=True)

    st.dataframe(lat, hide_index=True, use_container_width=True)

    st.subheader("Rank check")
    st.markdown(
        '<div class="note">A KLL sketch aims to be accurate in <b>rank</b>: how far along the sorted data a value sits. '
        "A small rank gap can still be a big gap in milliseconds when the data is sparse, like the slow tail.</div>",
        unsafe_allow_html=True,
    )
    r1, r2, r3 = st.columns(3)
    for col, (name, target, key) in zip([r1, r2, r3], [("p50", 50, "r50"), ("p90", 90, "r90"), ("p99", 99, "r99")]):
        rank = float(ranks[key]) * 100
        col.markdown(
            kpi(f"Sketch {name} = {int(approx[name]):,} ms", f"{rank:.2f}%",
                f"of real events are at or below it (target {target}%)",
                f"{rank - target:+.2f} pts", "good" if abs(rank - target) < 1 else "warn"),
            unsafe_allow_html=True,
        )
    st.write("")
    summary = pd.concat([
        pd.DataFrame([{"metric": "distinct_visitors", "exact": d_exact, "sketch": d_approx,
                       "difference": d_approx - d_exact, "error_pct": round(d_err, 3)}]),
        lat.rename(columns={"exact_ms": "exact", "sketch_ms": "sketch", "difference_ms": "difference"}),
    ])
    st.download_button("Download results (CSV)", summary.to_csv(index=False), "sketchlab_results.csv", "text/csv")

# ---------------- tab 2: why merging works ----------------
with tab2:
    st.subheader("Unique visitors: three ways to answer")
    naive_sum = float(approx["naive_sum_distinct"])
    cmp_df = pd.DataFrame({
        "method": ["Exact count", "Merged Theta sketch", "Add up daily estimates"],
        "visitors": [d_exact, d_approx, naive_sum],
    })
    base = alt.Chart(cmp_df).encode(
        y=alt.Y("method:N", sort=None, title=None),
        x=alt.X("visitors:Q", title="distinct visitors"),
    )
    bars = base.mark_bar(cornerRadiusEnd=6).encode(
        color=alt.Color("method:N", scale=alt.Scale(domain=list(cmp_df["method"]), range=[SLATE, INDIGO, RED]),
                        legend=None),
        tooltip=["method", alt.Tooltip("visitors:Q", format=",.0f")],
    )
    labels = base.mark_text(align="left", dx=5).encode(text=alt.Text("visitors:Q", format=",.0f"),
                                                       color=alt.value("#9ca3af"))
    st.altair_chart((bars + labels).properties(height=190), use_container_width=True)
    over = pct(naive_sum, d_exact)
    st.markdown(
        f'<div class="note">Adding up the stored daily estimates gives <b>{int(naive_sum):,}</b>, which is '
        f"<b>{over:+.1f}%</b> away from the exact count. The same visitor is counted again for every day and "
        "campaign they appear in. Merging the Theta sketches is a set union, so each visitor counts once.</div>",
        unsafe_allow_html=True,
    )

    st.subheader("Day by day")
    dd = pd.DataFrame(daily_exact).merge(pd.DataFrame(daily_approx), on="event_date")
    dd["day"] = pd.to_datetime(dd["event_date"]).dt.strftime("%b %d")
    day_long = dd.melt(id_vars="day", value_vars=["exact", "approx"], var_name="kind", value_name="visitors")
    day_long["kind"] = day_long["kind"].map({"exact": "Exact", "approx": "Sketch"})
    line = (
        alt.Chart(day_long)
        .mark_line(point=True)
        .encode(
            x=alt.X("day:N", sort=list(dd["day"]), title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("visitors:Q", title="distinct visitors per day"),
            color=alt.Color("kind:N", scale=alt.Scale(domain=["Exact", "Sketch"], range=[SLATE, INDIGO]), title=None),
            tooltip=["day", "kind", alt.Tooltip("visitors:Q", format=",.0f")],
        )
        .properties(height=260)
    )
    st.altair_chart(line, use_container_width=True)
    st.markdown(
        f'<div class="note">If you add the exact daily distinct counts above you get <b>{int(dd["exact"].sum()):,}</b>, '
        f"but the true distinct count for the whole range is <b>{int(d_exact):,}</b>. Even exact daily numbers "
        "cannot be added.</div>",
        unsafe_allow_html=True,
    )

    st.subheader("Percentiles: merging vs averaging")
    pr = []
    for name in ["p50", "p90", "p99"]:
        e, m, n = float(exact[name]), float(approx[name]), float(approx[f"naive_{name}"])
        pr.append({"metric": name, "exact": e, "merged sketch": m, "average of daily p-values": round(n, 1),
                   "merged error %": round(pct(m, e), 3), "averaged error %": round(pct(n, e), 3)})
    st.dataframe(pd.DataFrame(pr), hide_index=True, use_container_width=True)
    st.caption("Averaging ignores how many events each group holds. In this synthetic data every day follows the "
               "same pattern, so averaging can look fine or even closer. That is a property of this dataset, not "
               "a reason to trust it.")

# ---------------- tab 3: sketch size ----------------
with tab3:
    st.subheader("Serialized size in bytes")
    z1, z2, z3 = st.columns(3)
    z1.markdown(kpi("Merged Theta sketch", f"{int(approx['merged_theta_bytes']):,} B", "distinct visitors"),
                unsafe_allow_html=True)
    z2.markdown(kpi("Merged KLL sketch", f"{int(approx['merged_kll_bytes']):,} B", "latency percentiles"),
                unsafe_allow_html=True)
    z3.markdown(kpi(f"{sketch_rows} stored daily sketches", f"{daily_bytes:,} B", "Theta + KLL, before merging"),
                unsafe_allow_html=True)
    st.write("")
    size_df = pd.DataFrame({
        "what": ["Merged Theta", "Merged KLL", "All stored daily sketches"],
        "bytes": [int(approx["merged_theta_bytes"]), int(approx["merged_kll_bytes"]), daily_bytes],
    })
    size_chart = (
        alt.Chart(size_df)
        .mark_bar(cornerRadiusEnd=6)
        .encode(
            y=alt.Y("what:N", sort=None, title=None),
            x=alt.X("bytes:Q", scale=alt.Scale(type="log"), title="bytes (log scale)"),
            color=alt.Color("what:N", scale=alt.Scale(domain=list(size_df["what"]), range=[INDIGO, CYAN, SLATE]),
                            legend=None),
            tooltip=["what", alt.Tooltip("bytes:Q", format=",.0f")],
        )
        .properties(height=190)
    )
    st.altair_chart(size_chart, use_container_width=True)
    ratio = daily_bytes / merged_bytes if merged_bytes else 0
    st.markdown(
        f'<div class="note">For this selection, the stored daily sketches are about <b>{ratio:,.0f}x</b> larger than '
        "the single merged sketch. I have not measured the raw table's size, so this does not claim a saving "
        "against the raw events. Sketch size depends on the sketch settings and the data.</div>",
        unsafe_allow_html=True,
    )

# ---------------- tab 4: how it works ----------------
with tab4:
    with st.expander("What is a sketch?", expanded=True):
        st.markdown(
            "A sketch is a small, specially designed summary stored as binary. It does not keep a readable list of "
            "your records. It keeps just enough to estimate one kind of answer, and compatible sketches can be merged."
        )
    with st.expander("Theta sketch: counting distinct visitors"):
        st.markdown(
            "Each visitor ID is turned into a number (a hash). The sketch keeps only the smallest hashes. "
            "A repeat visitor gives the same hash, so it is stored once. The count is estimated from how many "
            "hashes are kept. With few distinct values nothing is dropped, so the answer can be exact. "
            "This is the general DataSketches idea."
        )
    with st.expander("KLL sketch: percentiles"):
        st.markdown(
            "A KLL sketch keeps sorted sample values with weights. Percentiles are read by walking the samples "
            "and adding up their weights. Its accuracy promise is about rank, so in a sparse tail the millisecond "
            "value can move more."
        )
    with st.expander("Why can't I add daily counts or average daily percentiles?"):
        st.markdown(
            "A daily distinct count has lost the information about who the visitors were, so overlapping visitors "
            "get counted again. A percentile depends on the whole distribution and on how many events each group "
            "holds, so averaging percentiles is generally not the percentile of the combined data."
        )
    with st.expander("When should I use exact calculations?"):
        st.markdown(
            "Whenever the number must be exact, for example finance, billing or compliance evidence. "
            "Sketches suit decision-support questions where a small error is acceptable."
        )

st.caption("Synthetic data. Results come from this dataset and are not general guarantees. "
           "Query speed is not measured here. Badge colours use simple display thresholds "
           "(under 2% green, under 5% amber) that I chose for readability.")
