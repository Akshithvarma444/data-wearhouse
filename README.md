# Data Warehouse projects

Two versions of the same e-commerce sales data warehouse assignment.

| Folder | What it is |
|---|---|
| [`workshop hackthon/`](workshop%20hackthon/) | Final version: React + TypeScript front end, FastAPI backend, PostgreSQL 18, Docker Compose. Start here. |
| [`ecommerce-data-warehouse/`](ecommerce-data-warehouse/) | First version: the same ETL pipeline as a Python command-line project, with a static HTML report. |

Both load generated e-commerce data (customers, products, orders, order items) into a
star schema (`fact_sales` with customer, product and date dimensions, SCD Type 2 on
customers and products), quarantine invalid rows, reconcile every run against the source
and can be re-run without creating duplicates.

Setup instructions are in each folder's README:

* [workshop hackthon/README.md](workshop%20hackthon/README.md)
* [ecommerce-data-warehouse/README.md](ecommerce-data-warehouse/README.md)
